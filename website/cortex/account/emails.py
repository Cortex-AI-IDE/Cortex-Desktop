"""
Branded transactional emails for the account app.

One shared OTP template (cortex/emails/otp_email.html) serves both signup
verification and password reset — HTML part for modern clients, plain-text
part as the fallback every mail client can render. Sender identity comes
from settings.NOTIFICATION_EMAIL (notification@cortex-ide.app).

TRANSPORT — Brevo HTTP API first, SMTP fallback:
DigitalOcean blocks outbound SMTP ports (25/465/587) on droplets, so
production sends via Brevo's HTTP API over HTTPS:443 (same approach as
CodeVisualizer's otp_service.py — POST https://api.brevo.com/v3/smtp/email
with the api-key header). When BREVO_API_KEY is not set (e.g. local dev),
emails fall back to Django's normal SMTP/console backend. The email DESIGN
(templates, PDF invoice attachment) is identical on both paths.
"""

import base64
import logging

import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger("api")

OTP_EXPIRY_MINUTES = 10

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
SENDER_NAME = "Cortex AI IDE"


def _send_email(subject: str, text_body: str, html_body: str, to_email: str,
                attachments=None) -> bool:
    """Send an email — Brevo HTTP API when configured, SMTP otherwise.

    attachments: list of (filename, content_bytes, mimetype) tuples.
    Never raises — a mail failure must not break signup/reset/payment flows.
    """
    api_key = getattr(settings, "BREVO_API_KEY", "")

    if api_key:
        # ── Brevo HTTP API (HTTPS:443 — not affected by SMTP port blocks) ──
        try:
            payload = {
                "sender": {"name": SENDER_NAME, "email": settings.NOTIFICATION_EMAIL},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": html_body,
                "textContent": text_body,
            }
            if attachments:
                payload["attachment"] = [
                    {"name": fname, "content": base64.b64encode(content).decode("ascii")}
                    for fname, content, _mimetype in attachments
                ]
            resp = requests.post(
                BREVO_API_URL,
                json=payload,
                headers={
                    "accept": "application/json",
                    "content-type": "application/json",
                    "api-key": api_key,
                },
                timeout=30,
            )
            if resp.status_code == 201:
                return True
            logger.warning("Brevo send failed (%s): %s — to=%s subject=%r",
                           resp.status_code, resp.text[:300], to_email, subject)
            return False
        except Exception:
            logger.warning("Brevo send raised — to=%s subject=%r",
                           to_email, subject, exc_info=True)
            return False

    # ── SMTP / console fallback (local dev, or Brevo key not set) ──
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.NOTIFICATION_EMAIL,
            to=[to_email],
        )
        msg.attach_alternative(html_body, "text/html")
        for fname, content, mimetype in (attachments or []):
            msg.attach(fname, content, mimetype)
        msg.send(fail_silently=True)
        return True
    except Exception:
        logger.warning("SMTP send raised — to=%s subject=%r",
                       to_email, subject, exc_info=True)
        return False


def send_otp_email(user, otp: str, *, purpose: str) -> None:
    """Send a styled OTP email. purpose: 'signup' or 'password_reset'."""
    if purpose == "password_reset":
        subject = "Your Cortex password reset code"
        heading = "Reset your password"
        intro = "Use the code below to reset your Cortex account password."
        ignore_note = ("If you didn't request a password reset, you can safely "
                       "ignore this email — your password stays unchanged.")
    else:
        subject = "Your Cortex verification code"
        heading = "Verify your email"
        intro = "Use the code below to finish creating your Cortex account."
        ignore_note = ("If you didn't create a Cortex account, you can safely "
                       "ignore this email.")

    display_name = user.get_display_name_or_email()

    text_body = (
        f"Hi {display_name},\n\n"
        f"{intro}\n\n"
        f"Your code is: {otp}\n\n"
        f"This code expires in {OTP_EXPIRY_MINUTES} minutes.\n"
        f"{ignore_note}\n\n"
        f"— Cortex AI IDE"
    )
    html_body = render_to_string("cortex/emails/otp_email.html", {
        "display_name": display_name,
        "otp": otp,
        "heading": heading,
        "intro": intro,
        "expiry_minutes": OTP_EXPIRY_MINUTES,
        "ignore_note": ignore_note,
    })

    _send_email(subject, text_body, html_body, user.email)


def send_invoice_email(user, order, payment, subscription) -> None:
    """Email the purchase receipt with the PDF tax invoice attached.

    Called from _activate_subscription() right after the Order is created —
    the idempotency guard there guarantees exactly one email per purchase.
    Fully fail-safe: an email/PDF error must NEVER break payment activation.
    """
    try:
        from io import BytesIO
        from django.utils import timezone
        from xhtml2pdf import pisa

        invoice_no = f"INV-{order.id:05d}"
        display_name = (user.get_full_name() or "").strip() or user.email
        reference = payment.paypal_order_id or payment.razorpay_order_id or "-"
        gateway = payment.get_gateway_display()
        amount = f"${order.amount_usd:.2f}"
        period_start = subscription.current_period_start.strftime("%b %d, %Y")
        period_end = subscription.current_period_end.strftime("%b %d, %Y")

        # ── Render the PDF invoice (same template the account page uses) ──
        invoice_html = render_to_string("cortex/account/invoice.html", {
            "order": order,
            "user": user,
            "subscription": subscription,
            "payment": payment,
            "now": timezone.now(),
        })
        pdf_buf = BytesIO()
        pisa_status = pisa.CreatePDF(invoice_html, dest=pdf_buf, encoding="utf-8")
        pdf_bytes = pdf_buf.getvalue() if not pisa_status.err else None

        # ── Branded receipt email ──
        text_body = (
            f"Hi {display_name},\n\n"
            f"Payment received — thank you for your purchase!\n\n"
            f"Plan: {order.item}\n"
            f"Amount: {amount} {order.currency}\n"
            f"Paid via: {gateway}\n"
            f"Reference: {reference}\n"
            f"Billing period: {period_start} - {period_end}\n\n"
            f"Your tax invoice {invoice_no} is attached as a PDF. You can also "
            f"download it anytime from https://cortex-ide.app/account/plan/\n\n"
            f"— Cortex AI IDE / PASONS INVESTMENT LLC"
        )
        html_body = render_to_string("cortex/emails/purchase_email.html", {
            "display_name": display_name,
            "plan": order.item,
            "amount": amount,
            "currency": order.currency,
            "gateway": gateway,
            "reference": reference,
            "period_start": period_start,
            "period_end": period_end,
            "invoice_no": invoice_no,
        })

        attachments = []
        if pdf_bytes:
            attachments.append(
                (f"Cortex_Invoice_{order.id:05d}.pdf", pdf_bytes, "application/pdf")
            )

        _send_email(
            f"Payment received — your Cortex invoice {invoice_no}",
            text_body, html_body, user.email, attachments=attachments,
        )
    except Exception:
        logger.warning(
            "Invoice email failed for order %s — payment itself is unaffected",
            getattr(order, "id", "?"), exc_info=True,
        )
