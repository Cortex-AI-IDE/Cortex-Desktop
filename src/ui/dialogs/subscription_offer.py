"""The one-time Cortex Pro offer card, shown after login to free accounts.

Cortex already requires a login (src/core/login_gate.py). This is the step
after it: a signed-in account that has NO active subscription sees a single
card offering the two plans the website sells - Pro Monthly $5 and Pro
Yearly $40 - with a way out. The rules, exactly as specified:

    subscribed              -> the card is never constructed, and the
                               Help-menu entry hides itself
    not subscribed, launch  -> the card shows ONCE, a beat after the IDE
                               paints so it never blocks startup or login
    Get Started             -> cortex-ide.app/pricing/ opens in the user's
                               real browser (pay & go); the card waits with
                               a "check again" that re-reads /auth/me and
                               unlocks everything the moment it sees active
    Skip / Esc / X          -> the card closes and stays closed for the
                               rest of this launch; the IDE is fully usable
    Help -> Upgrade to Pro  -> reopens the card at any time (force=True),
                               so skipping is never a dead end

The verdict comes from CortexAPIClient.has_subscription(), which is
offline-tolerant by design: it answers from the saved account copy and
refreshes behind the scenes, so a slow or unreachable cortex-ide.app can
show the card to a subscriber at worst once - never lock them out.

Visual language: this window belongs to the IDE. It reuses the login
window's palette and brand-face stack verbatim (src/ui/dialogs/login_dialog.py
states the rule - the site's lime is deliberately not used in-app) and mirrors
the pricing page's CONTENT and hierarchy: featured Monthly card with a
discount badge and strikethrough anchor price, quieter Yearly card beside it.
Native PyQt6 widgets, no webview: fast to construct, theme-safe, testable.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout, QWidget)

from src.ui.dialogs.login_dialog import _ui_font

log = logging.getLogger(__name__)

# The website's pricing palette (cortex-djnago cortex/static/cortex/css/
# pricing.css), so the card in the IDE and the card on cortex-ide.app are the
# same two cards: a dark featured plan with lime accents, a white plan beside
# it, on the site's light page colour (#ecf0ec, sampled from the live page).
PAGE = "#ecf0ec"
INK = "#161c25"
INK_MUTED = "#596170"
LINE = "#e4e7e8"
DARK = "#151c2b"
LIME = "#b7ef38"
LIME_HI = "#d2fb72"
LINK = "#365711"
SUCCESS = "#2f7d32"

PRICING_URL = "https://cortex-ide.app/pricing/"

# Mirrors https://cortex-ide.app/pricing/ plan-for-plan: names, badges,
# anchor prices, per-seat units and benefit bullets. The website stays the
# source of truth for checkout; this is only the in-app mirror of it.
PLANS = (
    {
        "id": "monthly",
        "name": "Pro Monthly",
        "badge": "50% off",
        "anchor": "$10",
        "price": "$5",
        "per": "/seat/month",
        "features": (
            "Web search included",
            "Voice prompts (speech to text)",
            "Semantic code search",
            "MCP server connections",
            "All LLM models via BYOK",
            "Full IDE features",
        ),
        "cta": "Get Started — $5/mo",
        "featured": True,
    },
    {
        "id": "yearly",
        "name": "Pro Yearly",
        "badge": "Save 67%",
        "anchor": "$120",
        "price": "$40",
        "per": "/seat/year",
        "features": (
            "Everything in Pro Monthly",
            "Save $80 a year",
            "Priority support",
            "Early-access features",
        ),
        "cta": "Get Started — $40/yr",
        "featured": False,
    },
)



def offer_decision(subscribed: bool, shown_this_session: bool,
                   force: bool = False) -> bool:
    """Whether the offer card should appear right now. Qt-free on purpose:
    this is the whole policy, and it is the part worth having tests for.

    force=True is the Help-menu path: it bypasses the once-per-launch flag
    (skipping must never be a dead end) but never overrides a subscription.
    """
    if subscribed:
        return False
    if force:
        return True
    return not shown_this_session


# Once per launch. Module-level because the decision outlives any single
# dialog instance: Skip closes the widget, the flag remembers the choice.
_shown_this_session = False


def reset_offer_session() -> None:
    """Forget this launch's show/skip. Tests, and nothing else, need it."""
    global _shown_this_session
    _shown_this_session = False


def maybe_show_offer(parent: QWidget | None = None, force: bool = False) -> bool:
    """Show the card iff the policy says so. Returns True when it was shown.

    Safe to call from a QTimer at startup: every failure mode (no API
    client, unreadable account) falls through to either "show once,
    skippable" or "stay silent", never to a crash and never to a lockout.
    """
    global _shown_this_session
    try:
        from src.core.cortex_api import get_api_client
        api = get_api_client()
        subscribed = bool(api.has_subscription())
    except Exception as exc:
        log.debug(f"[ProOffer] subscription status unavailable: {exc}")
        api, subscribed = None, False

    if not offer_decision(subscribed, _shown_this_session, force):
        return False

    _shown_this_session = True
    log.info(f"[ProOffer] showing offer card (force={force})")
    dialog = SubscriptionOfferDialog(parent, api=api)
    dialog.exec()
    return True


class _StatusWorker(QThread):
    """Re-read /auth/me off the GUI thread after a browser checkout.

    Mirrors CortexAPIClient.refresh_user_info_async but with a completion
    signal: the card needs to know WHEN the answer landed, not just that a
    refresh was kicked off. Merges the fresh account copy into the client
    (under its own lock, then persisted) so every other subscription gate -
    web search, voice, MCP, embeddings - unlocks in the same instant.
    """
    done = pyqtSignal(bool)

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self._api = api

    def run(self):
        try:
            me = self._api.get_me()
            if isinstance(me, dict) and me.get("email"):
                merged = {**(self._api.user_info or {}), **me}
                lock = getattr(self._api, "_lock", None)
                if lock is not None:
                    with lock:
                        self._api.user_info = merged
                else:
                    self._api.user_info = merged
                save = getattr(self._api, "_save_tokens", None)
                if callable(save):
                    save()
            self.done.emit(bool(self._api.has_subscription()))
        except Exception as exc:
            log.debug(f"[ProOffer] account re-check failed: {exc}")
            self.done.emit(False)


class SubscriptionOfferDialog(QDialog):
    """The two-plan Pro card. Modal, but every exit is a real exit: Skip,
    Esc and the window close all reject cleanly - showing the offer must
    never be able to trap someone outside their own IDE."""

    REASSURE = ("Cancel anytime from your account page. Skipping keeps "
                "Cortex fully usable with your own API keys.")
    WAITING = ("Checkout opened in your browser. Complete the payment "
               "there, then press “Check again” — your features unlock "
               "the moment the server confirms.")
    CHECKING = "Checking your account…"
    ACTIVE = ("Cortex Pro is active — web search, voice prompts, semantic "
              "search, MCP servers and every included model are unlocked.")
    NOT_FOUND = ("No active subscription on this account yet. If you just "
                 "paid, give it a minute and check again.")

    def __init__(self, parent: QWidget | None = None, api=None):
        super().__init__(parent)
        self._api = api
        self._worker = None
        self._drag_pos = None

        self.setModal(True)
        self.setWindowTitle("Cortex Pro")
        self.setWindowFlags(Qt.WindowType.Dialog
                            | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(680)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setObjectName("offerCard")
        outer.addWidget(card)

        box = QVBoxLayout(card)
        box.setContentsMargins(30, 26, 30, 22)
        box.setSpacing(14)

        box.addLayout(self._build_header())
        box.addLayout(self._build_plans())

        self._status = QLabel(self.REASSURE)
        self._status.setObjectName("offerStatus")
        self._status.setWordWrap(True)
        box.addWidget(self._status)

        box.addLayout(self._build_footer())

        self.setStyleSheet(self._stylesheet())

    # ── construction ────────────────────────────────────────────────────

    def _build_header(self) -> QVBoxLayout:
        # Stacked, not side-by-side: beside the 22px title the value prop
        # collapsed into a three-line sliver; full-width it reads as one
        # sentence, which is what it is.
        col = QVBoxLayout()
        col.setSpacing(6)
        title = QLabel("Cortex Pro")
        title.setObjectName("offerTitle")
        sub = QLabel("One subscription unlocks the connected IDE — web "
                     "search, voice prompts, semantic code search, MCP "
                     "servers and every included model.")
        sub.setObjectName("offerSub")
        sub.setWordWrap(True)
        col.addWidget(title)
        col.addWidget(sub)
        return col

    def _build_plans(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        for plan in PLANS:
            row.addWidget(self._plan_card(plan), 1)
        return row

    def _plan_card(self, plan: dict) -> QFrame:
        frame = QFrame()
        frame.setObjectName(f"plan_{plan['id']}")
        box = QVBoxLayout(frame)
        box.setContentsMargins(18, 16, 18, 18)
        box.setSpacing(9)

        badge = QLabel(plan["badge"])
        badge.setObjectName(f"badge_{plan['id']}")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedWidth(badge.sizeHint().width() + 20)
        box.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)

        name = QLabel(plan["name"])
        name.setObjectName("planName")
        box.addWidget(name)

        price_row = QHBoxLayout()
        price_row.setSpacing(8)
        anchor = QLabel(plan["anchor"])
        anchor.setObjectName("priceAnchor")
        price = QLabel(plan["price"])
        price.setObjectName("priceBig")
        per = QLabel(plan["per"])
        per.setObjectName("pricePer")
        price_row.addWidget(anchor)
        price_row.addWidget(price)
        price_row.addWidget(per)
        price_row.addStretch(1)
        box.addLayout(price_row)

        hair = QFrame()
        hair.setObjectName("planHair")
        hair.setFixedHeight(1)
        box.addWidget(hair)

        for feature in plan["features"]:
            frow = QHBoxLayout()
            frow.setSpacing(8)
            tick = QLabel("✓")
            tick.setObjectName("featureTick")
            tick.setFixedSize(18, 18)
            tick.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text = QLabel(feature)
            text.setObjectName("featureText")
            text.setWordWrap(True)
            frow.addWidget(tick, 0, Qt.AlignmentFlag.AlignTop)
            frow.addWidget(text, 1)
            box.addLayout(frow)

        box.addStretch(1)

        cta = QPushButton(plan["cta"])
        cta.setObjectName(f"cta_{plan['id']}")
        cta.setCursor(Qt.CursorShape.PointingHandCursor)
        cta.clicked.connect(lambda _=False, pid=plan["id"]: self._on_get_started(pid))
        box.addWidget(cta)
        return frame

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        check = QPushButton("Already subscribed? Check again")
        check.setObjectName("checkAgain")
        check.setCursor(Qt.CursorShape.PointingHandCursor)
        check.clicked.connect(self._on_check_again)
        skip = QPushButton("Skip for now")
        skip.setObjectName("skip")
        skip.setCursor(Qt.CursorShape.PointingHandCursor)
        skip.clicked.connect(self.reject)
        row.addWidget(check)
        row.addStretch(1)
        row.addWidget(skip)
        return row

    def _stylesheet(self) -> str:
        # Per-card rules use descendant selectors: both cards reuse the same
        # label names (planName, priceBig, ...) and only the card differs.
        M, Y = "QFrame#plan_monthly", "QFrame#plan_yearly"
        return f"""
        QDialog {{ background: transparent; }}
        QFrame#offerCard {{
            background: {PAGE};
            border: 1px solid {LINE};
            border-radius: 14px;
        }}
        QLabel#offerTitle {{ color: {INK}; }}
        QLabel#offerSub {{ color: {INK_MUTED}; }}
        QLabel#offerStatus {{ color: {INK_MUTED}; }}

        {M} {{
            background: {DARK};
            border: 1px solid {DARK};
            border-radius: 14px;
        }}
        {M} QLabel#badge_monthly {{
            background: {LIME}; color: {DARK};
            border-radius: 6px; padding: 4px 0px;
        }}
        {M} QLabel#planName {{ color: {LIME}; }}
        {M} QLabel#priceAnchor {{ color: #c4ccd9; }}
        {M} QLabel#priceBig {{ color: #ffffff; }}
        {M} QLabel#pricePer {{ color: #c4ccd9; }}
        {M} QFrame#planHair {{ background: rgba(255, 255, 255, 0.16); border: none; }}
        {M} QLabel#featureTick {{
            background: {LIME}; color: {DARK}; border-radius: 9px;
        }}
        {M} QLabel#featureText {{ color: #e1e6ed; }}
        QPushButton#cta_monthly {{
            background: {LIME}; color: {DARK};
            border: 1px solid {LIME}; border-radius: 8px; padding: 10px 12px;
        }}
        QPushButton#cta_monthly:hover {{ background: {LIME_HI}; border-color: {LIME_HI}; }}
        QPushButton#cta_monthly:pressed {{ background: #a5d92f; border-color: #a5d92f; }}

        {Y} {{
            background: #ffffff;
            border: 1px solid {LINE};
            border-radius: 14px;
        }}
        {Y} QLabel#badge_yearly {{
            background: #edf5d7; color: {LINK};
            border-radius: 6px; padding: 4px 0px;
        }}
        {Y} QLabel#planName {{ color: {INK}; }}
        {Y} QLabel#priceAnchor {{ color: {INK_MUTED}; }}
        {Y} QLabel#priceBig {{ color: {INK}; }}
        {Y} QLabel#pricePer {{ color: {INK_MUTED}; }}
        {Y} QFrame#planHair {{ background: {LINE}; border: none; }}
        {Y} QLabel#featureTick {{
            background: #dcf2bc; color: #234318; border-radius: 9px;
        }}
        {Y} QLabel#featureText {{ color: #424b58; }}
        QPushButton#cta_yearly {{
            background: {DARK}; color: #ffffff;
            border: 1px solid {DARK}; border-radius: 8px; padding: 10px 12px;
        }}
        QPushButton#cta_yearly:hover {{ background: #303d50; border-color: #303d50; }}
        QPushButton#cta_yearly:pressed {{ background: #0e1420; }}

        QPushButton#checkAgain {{
            background: transparent; color: {LINK};
            border: none; padding: 6px 2px; text-align: left;
        }}
        QPushButton#checkAgain:hover {{ color: #22400a; }}
        QPushButton#checkAgain:disabled {{ color: {INK_MUTED}; }}
        QPushButton#skip {{
            background: transparent; color: {INK_MUTED};
            border: none; padding: 6px 10px;
        }}
        QPushButton#skip:hover {{ color: {INK}; }}
        """

    # ── behaviour ─────────────────────────────────────────────────────────

    def _apply_fonts(self) -> None:
        """Type sizes/tracking, set in code: QSS font rules would fight the
        brand-face stack from _ui_font instead of composing with it."""
        f = _ui_font
        self.findChild(QLabel, "offerTitle").setFont(f(22, 800, -0.4))
        self.findChild(QLabel, "offerSub").setFont(f(13, 400))
        self.findChild(QLabel, "offerStatus").setFont(f(11, 400))
        for plan in PLANS:
            pid = plan["id"]
            self.findChild(QLabel, f"badge_{pid}").setFont(f(11, 700))
            self.findChild(QPushButton, f"cta_{pid}").setFont(f(13, 700))
        for label in self.findChildren(QLabel, "planName"):
            label.setFont(f(15, 700))
        for label in self.findChildren(QLabel, "priceAnchor"):
            font = f(13, 500)
            font.setStrikeOut(True)
            label.setFont(font)
        for label in self.findChildren(QLabel, "priceBig"):
            label.setFont(f(34, 800, -0.8))
        for label in self.findChildren(QLabel, "pricePer"):
            label.setFont(f(11, 400))
        for label in self.findChildren(QLabel, "featureTick"):
            label.setFont(f(12, 700))
        for label in self.findChildren(QLabel, "featureText"):
            label.setFont(f(12, 400))
        self.findChild(QPushButton, "checkAgain").setFont(f(12, 500))
        self.findChild(QPushButton, "skip").setFont(f(12, 500))

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_fonts()

    def _set_status(self, text: str, color: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    def _on_get_started(self, plan_id: str) -> None:
        """Pay & go: the website holds checkout, the card just hands the
        user to it and waits. Both plans land on the same pricing page."""
        log.info(f"[ProOffer] Get Started ({plan_id}) → {PRICING_URL}")
        QDesktopServices.openUrl(QUrl(PRICING_URL))
        self._set_status(self.WAITING, INK_MUTED)

    def _on_check_again(self) -> None:
        if self._api is None:
            try:
                from src.core.cortex_api import get_api_client
                self._api = get_api_client()
            except Exception as exc:
                log.debug(f"[ProOffer] no API client for re-check: {exc}")
                self._set_status(self.NOT_FOUND, INK_MUTED)
                return
        self.findChild(QPushButton, "checkAgain").setEnabled(False)
        self._set_status(self.CHECKING, INK_MUTED)
        self._worker = _StatusWorker(self._api, self)
        self._worker.done.connect(self._on_status_done)
        self._worker.start()

    def _on_status_done(self, active: bool) -> None:
        self._worker = None
        self.findChild(QPushButton, "checkAgain").setEnabled(True)
        if active:
            log.info("[ProOffer] subscription confirmed, closing card")
            self._set_status(self.ACTIVE, SUCCESS)
            QTimer.singleShot(1400, self.accept)
        else:
            self._set_status(self.NOT_FOUND, INK_MUTED)

    # ── frameless window plumbing ─────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.pos())
            if not isinstance(child, QPushButton):
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)
