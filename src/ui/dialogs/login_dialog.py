"""The full-screen window Cortex shows when nobody is signed in.

Shown before the main window exists, so it cannot use anything the IDE sets
up. That is exactly why it must not hardcode a palette either: the first
thing a user sees has to look like the product they configured, not like a
different app that happens to share the logo.

  colours    read from the SAME source the rest of Cortex reads, in this
             order:
               1. Settings -> Appearance -> Color theme. A named theme
                  (Tokyo Night, Dracula, High Contrast Light, ...) carries
                  its own `ui` palette in src/ui/color_themes.py, so those
                  values are used directly.
               2. Otherwise "Cortex (Dark / Light / System)", resolved from
                  the saved mode through src/ui/tokens.py, which is what
                  main.py already pointed at the saved theme before this
                  dialog is built.
             Nothing here is a literal colour. The old version painted
             #141417 / #06b6d4 regardless of settings, so a Tokyo Night or
             Light+ user met a dark grey sign-in card that belonged to no
             theme at all.

  contrast   every text colour is composited onto the surface it actually
             sits on and nudged until it clears WCAG AA (4.5:1). Tokens are
             authored for the app's own surfaces, and this screen puts text
             on a canvas, a card and a button, so the pairing has to be
             checked rather than assumed. The button's label colour is
             chosen from the accent's luminance: a mid-blue accent needs
             white ink, a yellow or lilac accent needs near-black.

  wordmark   the brand rule, matching the site's lockup: the emblem sits
             left of a two-line wordmark, "Cortex" at weight 700 with tight
             tracking over "AI IDE" at weight 700 with wide tracking. The
             emblem is sized against the two-line block, not against the
             window, so the mark reads as part of the word rather than a
             picture above a caption. The mark file is chosen by the
             background it sits on: brand_mark_dark.png is the LIGHT-ink
             mark (its name is the background, not the colour).

  emblem     scaled at the window's device pixel ratio, so it stays crisp on
             a HiDPI screen instead of being upsampled from logical pixels.

  canvas     a faint dot grid, the same visual language as an editor's
             canvas. It is drawn at 10% of the text colour, which is
             visible structure on an empty screen and disappears once the
             card is in front of it.

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
import re
import sys
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout,
                             QLabel, QProgressBar, QPushButton, QVBoxLayout,
                             QWidget)

log = logging.getLogger(__name__)

# ── layout metrics, kept in one place so the rhythm cannot drift ───────────
CARD_WIDTH      = 400
CARD_RADIUS     = 14
CARD_PAD_H      = 28
CARD_PAD_V      = 26
BUTTON_HEIGHT   = 44
BUTTON_RADIUS   = 10
MARK_PX         = 38      # emblem edge, sized against the wordmark block
GAP_MARK        = 14      # emblem -> wordmark
WORD_PX         = 28      # "Cortex"
GAP_WORD        = 3       # "Cortex" -> "AI IDE", an optical gap
SUB_PX          = 10      # "AI IDE"
SUB_TRACK       = 2.3     # "AI IDE" tracking, wide and uppercase
LOCKUP_TO_CARD  = 34
CARD_TO_HELP    = 16
STATUS_H        = 28
DOT_STEP        = 26      # dot-grid pitch on the canvas
DOT_SIZE        = 1


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
    # "Inter Variable" is the name Qt registers the BUNDLED Inter under
    # (src/ui/fonts.py); plain "Inter" is never in families(), so without it
    # the login screen skipped the bundled face and landed on Segoe UI.
    for name in ("Geist", "Inter", "Inter Variable",
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


# ── colour maths ───────────────────────────────────────────────────────────
# Qt stylesheets accept "#rrggbb" everywhere, but the app's tokens also use
# rgba() with a fractional alpha for text. Compositing to a solid hex here
# means every colour this screen applies is unambiguous, and it is what makes
# the contrast checks below measurable.

_RGB_RE = re.compile(r"rgba?\(\s*([^)]+)\)")


def _parse(color: str) -> tuple[int, int, int, float]:
    """'#abc' / '#aabbcc' / '#aabbccdd' / 'rgb(...)' / 'rgba(...)' -> RGBA."""
    c = str(color).strip()
    if c.startswith("#"):
        h = c[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) == 8:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16),
                    int(h[6:8], 16) / 255.0)
        if len(h) >= 6:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = _RGB_RE.match(c)
    if m:
        parts = [p.strip() for p in m.group(1).replace("/", " ").split(",")]
        if len(parts) >= 3:
            r, g, b = (int(float(p)) for p in parts[:3])
            a = 1.0
            if len(parts) >= 4:
                raw = parts[3]
                a = float(raw[:-1]) / 100.0 if raw.endswith("%") else float(raw)
            return r, g, b, max(0.0, min(1.0, a))
    raise ValueError(f"unsupported colour: {color!r}")


def _hex(rgb) -> str:
    r, g, b = (max(0, min(255, int(round(v)))) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _solid(color: str, over: str = "#000000") -> str:
    """`color` composited onto the opaque `over` -> '#rrggbb'."""
    r, g, b, a = _parse(color)
    if a >= 0.999:
        return _hex((r, g, b))
    br, bg, bb, _ = _parse(over)
    return _hex((r * a + br * (1 - a),
                 g * a + bg * (1 - a),
                 b * a + bb * (1 - a)))


def _mix(a: str, b: str, t: float) -> str:
    """`t` of the way from colour `a` to colour `b`."""
    t = max(0.0, min(1.0, t))
    ar, ag, ab, _ = _parse(a)
    br, bg, bb, _ = _parse(b)
    return _hex((ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t))


def _luminance(color: str) -> float:
    """WCAG relative luminance."""
    def channel(v: float) -> float:
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b, _ = _parse(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _best_ink(bg: str) -> str:
    """Black or white, whichever reads better on `bg`."""
    return ("#0b0b0d" if _contrast(bg, "#0b0b0d") >= _contrast(bg, "#ffffff")
            else "#ffffff")


def _readable(color: str, bg: str, target: float = 4.5) -> str:
    """`color` on `bg`, moved toward black or white until it clears `target`."""
    c = _solid(color, bg)
    if _contrast(c, bg) >= target:
        return c
    toward = ("#000000" if _contrast("#000000", bg) >= _contrast("#ffffff", bg)
              else "#ffffff")
    for i in range(1, 21):
        cand = _mix(c, toward, i / 20.0)
        if _contrast(cand, bg) >= target:
            return cand
    return toward


def _saved_theme_id() -> str:
    """The saved Settings -> Appearance -> Color theme id, "cortex" if none."""
    try:
        from src.ui.color_themes import THEMES, color_theme
        tid = color_theme()
        return tid if tid in THEMES else "cortex"
    except Exception as exc:
        log.debug("[Login] colour theme not readable: %s", exc)
        return "cortex"


def _saved_mode() -> str:
    """The saved Dark / Light / System mode, resolved to "dark" or "light"."""
    mode = "dark"
    try:
        from src.config.settings import get_settings
        mode = str(get_settings().theme or "dark").lower()
    except Exception as exc:
        log.debug("[Login] theme mode not readable: %s", exc)
    if mode not in ("dark", "light"):
        try:
            from src.config.theme_manager import _detect_system_theme
            mode = _detect_system_theme()
        except Exception:
            mode = "dark"
    return "light" if mode == "light" else "dark"


def theme_palette(tid: str | None = None) -> dict:
    """Every colour this screen paints, resolved for the saved appearance.

    `tid` is only for tests and screenshots; production passes nothing.
    """
    tid = tid or _saved_theme_id()
    if tid not in ("cortex",):
        try:
            from src.ui.color_themes import THEMES
            entry = THEMES[tid]
        except Exception:
            entry = None
        if entry:
            base = entry["base"]
            c = entry["ui"]
            canvas, canvas_alt = c["bg"], c["bg_alt"]
            surface, border = c["surface"], c["border"]
            fg, fg_dim, muted = c["fg"], c["fg_dim"], c["muted"]
            accent, danger, ok = c["accent"], c["red"], c["green"]
            return _assemble(base, canvas, canvas_alt, surface, border,
                             fg, fg_dim, muted, accent, danger, ok)

    # Cortex's own look: main.py has already pointed these palettes at the
    # saved Dark / Light / System mode (color_themes.apply_at_startup).
    from src.ui import tokens
    base = _saved_mode()
    t = tokens.get_theme(base)
    canvas = _solid(t["bg"], "#000000")
    return _assemble(
        base,
        canvas,
        _solid(t["bg_tertiary"], canvas),
        _solid(t["bg_card"], canvas),
        _solid(t["border"], canvas),
        _solid(t["text"], canvas),
        _solid(t["text_dim"], canvas),
        _solid(t["muted"], canvas),
        _solid(t["accent"], canvas),
        _solid(t["error"], canvas),
        _solid(t["green"], canvas),
    )


def _assemble(base: str, canvas: str, canvas_alt: str, surface: str,
              border: str, fg: str, fg_dim: str, muted: str, accent: str,
              danger: str, ok: str) -> dict:
    """Check the pairings this screen actually uses, then build the palette."""
    accent = _solid(accent, surface)
    toward = "#ffffff" if base == "dark" else "#000000"
    hover = _mix(accent, toward, 0.14)
    press = _mix(accent, toward, 0.26)

    off = _mix(accent, canvas, 0.80)
    return {
        "theme": base,
        "canvas": canvas,
        "canvas_alt": canvas_alt,
        "surface": surface,
        "border": border,
        "fg": _readable(fg, surface),
        "fg_dim": _readable(fg_dim, surface),
        # The help line sits on the canvas, not on the card.
        "muted": _readable(muted, canvas),
        "accent": accent,
        "accent_hover": hover,
        "accent_press": press,
        "accent_ink": _best_ink(accent),
        "btn_off": off,
        "btn_off_ink": _readable(fg_dim, off, 3.0),
        "danger": _readable(danger, canvas),
        "ok": _readable(ok, canvas),
        # Dot grid: structure you notice only when the screen is empty.
        "dots": _mix(canvas, fg, 0.10),
    }


def _qss(p: dict) -> str:
    """One stylesheet for the window, every rule pinned to an object name.

    A type selector set on a container cascades to its children (that is how
    a `QWidget { border-top: ... }` rule once drew a hairline across every
    unstyled row of the chat footer), so nothing here selects by type for a
    property that would inherit into a child.
    """
    return f"""
    QDialog#loginRoot {{ background: {p['canvas']}; }}
    QLabel {{ color: {p['fg']}; background: transparent; }}
    QFrame#loginCard {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: {CARD_RADIUS}px;
    }}
    QLabel#loginNote {{ color: {p['fg_dim']}; }}
    QLabel#loginHelp {{ color: {p['muted']}; }}
    QPushButton#loginPrimary {{
        background: {p['accent']};
        color: {p['accent_ink']};
        border: 2px solid transparent;
        border-radius: {BUTTON_RADIUS}px;
    }}
    QPushButton#loginPrimary:hover {{ background: {p['accent_hover']}; }}
    QPushButton#loginPrimary:pressed {{ background: {p['accent_press']}; }}
    QPushButton#loginPrimary:focus {{ border: 2px solid {p['fg']}; }}
    QPushButton#loginPrimary:disabled {{
        background: {p['btn_off']};
        color: {p['btn_off_ink']};
    }}
    QProgressBar#loginBusy {{
        background: {p['border']};
        border: none;
        border-radius: 1px;
    }}
    QProgressBar#loginBusy::chunk {{
        background: {p['accent']};
        border-radius: 1px;
    }}
    QFrame#loginBar {{
        background: {p['canvas_alt']};
        border: none;
        border-top: 1px solid {p['border']};
    }}
    QPushButton#loginGhost {{
        color: {p['muted']};
        background: transparent;
        border: none;
    }}
    QPushButton#loginGhost:hover {{ color: {p['fg']}; }}
    QPushButton#loginQuit {{
        color: {p['muted']};
        background: transparent;
        border: none;
    }}
    QPushButton#loginQuit:hover {{ color: {p['danger']}; }}
    """


# How long to keep waiting for someone to finish signing in in their browser.
# Generous on purpose: it can mean finding a password manager, or 2FA on a
# phone that is in another room.
BROWSER_TIMEOUT_SECONDS = 300


class _DotField(QWidget):
    """The empty canvas: the theme's background with a faint dot grid.

    Drawn rather than styled because QSS has no repeating pattern. The grid
    is the screen's one piece of structure: it makes the emptiness deliberate
    instead of an unfinished page, and it is gone the moment the card covers
    it.
    """

    def __init__(self, canvas: str, dots: str, parent=None):
        super().__init__(parent)
        self._canvas = QColor(canvas)
        self._dots = QColor(dots)
        self.setAutoFillBackground(False)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), self._canvas)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._dots)
            w, h = self.width(), self.height()
            y = DOT_STEP
            while y < h:
                x = DOT_STEP
                while x < w:
                    p.drawRect(x, y, DOT_SIZE, DOT_SIZE)
                    x += DOT_STEP
                y += DOT_STEP
        finally:
            p.end()


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
    # near-black (measured 10.9/255) and vanishes on a dark canvas.
    LOGO_ON_DARK = _asset("src/assets/logo/brand_mark_dark.png")
    LOGO_ON_LIGHT = _asset("src/assets/logo/brand_mark_light.png")

    def __init__(self, parent=None, *, theme_id: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Cortex AI IDE")
        self.setModal(True)
        self.setObjectName("loginRoot")
        self._worker: _LoginWorker | None = None
        self.user_info: dict | None = None
        self._p = theme_palette(theme_id)
        self.setStyleSheet(_qss(self._p))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = _DotField(self._p["canvas"], self._p["dots"])
        outer.addWidget(body, 1)
        col = QVBoxLayout(body)
        col.setContentsMargins(24, 24, 24, 24)
        col.setSpacing(0)
        col.addStretch(2)

        col.addWidget(self._build_brand(), 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(LOCKUP_TO_CARD)
        col.addWidget(self._build_card(), 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(CARD_TO_HELP)
        col.addWidget(self._build_help(), 0, Qt.AlignmentFlag.AlignHCenter)
        col.addStretch(3)

        outer.addWidget(self._build_status_bar())
        self._set_state("idle", "Not signed in")

    # ── brand ─────────────────────────────────────────────────────────────

    def _build_brand(self) -> QWidget:
        """Emblem left, two-line wordmark right: the site's lockup."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(GAP_MARK)

        mark = QLabel()
        mark.setStyleSheet("border: none;")
        path = (self.LOGO_ON_DARK if self._p["theme"] == "dark"
                else self.LOGO_ON_LIGHT)
        pix = QPixmap(path)
        if not pix.isNull():
            # Scale in DEVICE pixels so a HiDPI screen gets a real 38px mark
            # instead of a 38px image upsampled by a factor of two.
            dpr = self.devicePixelRatioF() or 1.0
            edge = int(round(MARK_PX * dpr))
            scaled = pix.scaled(edge, edge,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            mark.setPixmap(scaled)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)

        words = QWidget()
        v = QVBoxLayout(words)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(GAP_WORD)

        top = QLabel("Cortex")
        top.setFont(_ui_font(WORD_PX, 700, -WORD_PX * 0.022))
        top.setStyleSheet(f"color:{self._p['fg']};border:none;")
        v.addWidget(top)

        # AbsoluteSpacing advances the LAST glyph too, so the ink ends one
        # tracking step before the label's right edge. The stack is left
        # aligned and "Cortex" is wider than this line, so no centring
        # correction is needed, only the optical gap above.
        sub = QLabel("AI IDE")
        sub.setFont(_ui_font(SUB_PX, 700, SUB_TRACK))
        sub.setStyleSheet(f"color:{self._p['fg_dim']};border:none;")
        v.addWidget(sub)

        h.addWidget(words, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    # ── sign-in card ──────────────────────────────────────────────────────

    def _build_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("loginCard")
        card.setFixedWidth(CARD_WIDTH)
        v = QVBoxLayout(card)
        v.setContentsMargins(CARD_PAD_H, CARD_PAD_V, CARD_PAD_H, CARD_PAD_V)
        v.setSpacing(0)

        head = QLabel("Sign in")
        head.setFont(_ui_font(17, 700, -0.2))
        v.addWidget(head)

        v.addSpacing(7)

        note = QLabel("A Cortex account is required to use the IDE.")
        note.setObjectName("loginNote")
        note.setFont(_ui_font(12, 400))
        note.setWordWrap(True)
        v.addWidget(note)

        v.addSpacing(20)

        self._browser_btn = QPushButton("Continue in browser")
        self._browser_btn.setObjectName("loginPrimary")
        self._browser_btn.setFixedHeight(BUTTON_HEIGHT)
        self._browser_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browser_btn.setFont(_ui_font(13, 600))
        self._browser_btn.setDefault(True)
        self._browser_btn.setAccessibleName("Continue sign-in in your browser")
        self._browser_btn.clicked.connect(self._sign_in_browser)
        v.addWidget(self._browser_btn)

        self._busy = QProgressBar()
        self._busy.setObjectName("loginBusy")
        self._busy.setRange(0, 0)
        self._busy.setVisible(False)
        self._busy.setFixedHeight(2)
        self._busy.setTextVisible(False)
        v.addSpacing(8)
        v.addWidget(self._busy)
        return card

    def _build_help(self) -> QWidget:
        """Support line. It is a real link: the address is meant to be used."""
        self._help = QLabel()
        self._help.setObjectName("loginHelp")
        self._help.setFont(_ui_font(12, 400))
        self._help.setTextFormat(Qt.TextFormat.RichText)
        self._help.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction)
        self._help.setOpenExternalLinks(True)
        self._help.setText(
            f"Trouble signing in?&nbsp; "
            f"<a href='https://cortex-ide.app/support' "
            f"style='color:{self._p['accent']};text-decoration:none;'>"
            f"cortex-ide.app/support</a>")
        self._help.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return self._help

    # ── status bar ────────────────────────────────────────────────────────

    def _build_status_bar(self) -> QWidget:
        """The sign-in state, where a developer already looks for state.

        This is the IDE's status bar, so it is also where errors go: one
        place that always says what is true, instead of a red line that
        appears and vanishes.
        """
        bar = QFrame()
        bar.setObjectName("loginBar")
        bar.setFixedHeight(STATUS_H)
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 0, 14, 0)
        h.setSpacing(8)

        self._dot = QLabel("\u25cf")
        self._dot.setFont(_ui_font(10, 400))
        h.addWidget(self._dot)

        self._status = QLabel("")
        # The bundled code font (src/ui/fonts.py). This asked for Geist Mono,
        # then Ubuntu Mono - neither ships on Windows, so it fell to a
        # generic "monospace" (Courier New).
        from src.ui.fonts import mono_font
        mono = mono_font(9)
        mono.setPixelSize(12)
        self._status.setFont(mono)
        h.addWidget(self._status)
        h.addStretch(1)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("loginGhost")
        self._cancel_btn.setVisible(False)
        self._cancel_btn.setFlat(True)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setFont(_ui_font(12, 500))
        self._cancel_btn.clicked.connect(self._cancel_wait)
        h.addWidget(self._cancel_btn)

        quit_btn = QPushButton("Quit")
        quit_btn.setObjectName("loginQuit")
        quit_btn.setFlat(True)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setFont(_ui_font(12, 500))
        quit_btn.clicked.connect(self.reject)
        h.addWidget(quit_btn)
        return bar

    def _set_state(self, kind: str, message: str):
        colour = {"idle": self._p["muted"], "busy": self._p["accent"],
                  "error": self._p["danger"], "ok": self._p["ok"]}[kind]
        self._dot.setStyleSheet(f"color:{colour};border:none;")
        self._status.setStyleSheet(
            f"color:{self._p['fg'] if kind != 'idle' else self._p['muted']};"
            f"border:none;")
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
                    "Waiting for sign-in to finish in your browser\u2026")
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
