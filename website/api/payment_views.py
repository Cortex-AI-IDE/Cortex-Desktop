"""
Cortex Payment Views — PayPal & Razorpay Integration

Handles:
- PayPal: create-order (client-side), capture-order (client-side), webhook (backup)
- Razorpay: create-order (server-side), verify (server-side), webhook (backup)
- Subscription activation: shared _activate_subscription() for both gateways
- Payment result pages: success, cancel, failed

Dual-layer verification:
  Layer 1 (Primary):  JS callback → /verify endpoint → activate subscription
  Layer 2 (Backup):   PayPal/Razorpay webhook → /webhook endpoint → activate (idempotent)

If the user closes the browser after payment but before the JS callback fires,
the webhook catches it.
"""
import hashlib
import hmac
import json
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Payment, Subscription

logger = logging.getLogger("api")


# =============================================================================
# Plan Pricing Configuration
# =============================================================================

# USD prices (public pricing page)
PLAN_PRICES_USD = {
    Subscription.Plan.PRO: Decimal("10.00"),
    Subscription.Plan.PRO_YEARLY: Decimal("80.00"),
}

# INR prices (for Indian users via Razorpay)
PLAN_PRICES_INR = {
    Subscription.Plan.PRO: Decimal("899.00"),
    Subscription.Plan.PRO_YEARLY: Decimal("6999.00"),
}


def _get_plan_from_amount(amount, currency="USD"):
    """Determine plan from payment amount."""
    amount = Decimal(str(amount))
    if currency == "INR":
        for plan, price in PLAN_PRICES_INR.items():
            if amount == price:
                return plan
    else:
        for plan, price in PLAN_PRICES_USD.items():
            if amount == price:
                return plan
    return None


def _get_expected_amount(plan, currency="USD"):
    """Get the expected amount for a plan+currency from backend pricing dict.

    This is the SINGLE SOURCE OF TRUTH for prices. No frontend amount
    is ever trusted — always compare against this.
    """
    if currency == "INR":
        return PLAN_PRICES_INR.get(plan)
    return PLAN_PRICES_USD.get(plan)


def _validate_payment_amount(plan, amount, currency="USD"):
    """Validate that a given amount matches the backend price for the plan.

    Returns (is_valid, expected_amount). Rejects manipulated amounts.
    """
    expected = _get_expected_amount(plan, currency)
    if expected is None:
        return False, None
    actual = Decimal(str(amount))
    return actual == expected, expected


# =============================================================================
# PayPal Views
# =============================================================================

@login_required
@csrf_exempt
@require_POST
def paypal_create_order(request):
    """
    POST /payment/paypal/create-order/

    Records a PayPal order created CLIENT-SIDE by the JS SDK.
    The browser's PayPal SDK calls actions.order.create() first, then
    hits this endpoint to record the order on the server.

    Request body (JSON):
        order_id: PayPal order ID (e.g. "5O190127TN364715T")
        amount:   Payment amount (e.g. "10.00")
        currency: USD (default)

    Returns:
        201: {"status": "pending", "payment_id": ...}
        409: Duplicate pending payment
        400: Invalid data
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    order_id = data.get("order_id", "").strip()
    amount = data.get("amount", "0")
    currency = data.get("currency", "USD")
    plan_key = data.get("plan", "").strip().lower()

    if not order_id:
        return JsonResponse({"error": "order_id is required"}, status=400)

    # --- SERVER-SIDE PLAN RESOLUTION (never trust client amount) ---
    # Client sends plan name; server resolves the canonical price.
    # If plan is missing, try legacy amount-based lookup as fallback.
    if plan_key in [Subscription.Plan.PRO, Subscription.Plan.PRO_YEARLY]:
        plan = plan_key
        expected_amount = PLAN_PRICES_USD.get(plan)
        if expected_amount is None:
            return JsonResponse({"error": f"Plan {plan} not available for USD"}, status=400)
        # Reject if client sent a mismatched amount (tamper detection)
        if Decimal(str(amount)) != expected_amount:
            logger.critical(
                "PayPal create_order amount tampering: user=%s sent plan=%s amount=%s expected=%s",
                request.user.email, plan, amount, expected_amount,
            )
            return JsonResponse({"error": "Amount does not match plan"}, status=400)
        # Use server-verified amount
        amount = str(expected_amount)
    else:
        # Fallback: try to resolve from amount (legacy flow)
        plan = _get_plan_from_amount(amount, currency)
        if not plan:
            return JsonResponse({"error": f"Unknown plan for amount {amount} {currency}"}, status=400)
        # Verify against server price
        is_valid, expected = _validate_payment_amount(plan, amount, currency)
        if not is_valid:
            return JsonResponse({"error": "Amount does not match plan"}, status=400)

    # --- Auto-expire stale pending records (>10 min old) ---
    stale_cutoff = timezone.now() - timedelta(minutes=10)
    Payment.objects.filter(
        user=request.user,
        status=Payment.Status.PENDING,
        gateway=Payment.Gateway.PAYPAL,
        created_at__lt=stale_cutoff,
    ).update(status=Payment.Status.FAILED)

    # --- Pending dedup: block rapid retries within 10 minutes ---
    recent_pending = Payment.objects.filter(
        user=request.user,
        status=Payment.Status.PENDING,
        gateway=Payment.Gateway.PAYPAL,
        created_at__gte=stale_cutoff,
    ).exists()
    if recent_pending:
        return JsonResponse(
            {"error": "You have a pending payment. Please wait before trying again."},
            status=409,
        )

    # --- Already completed check ---
    already_completed = Payment.objects.filter(
        user=request.user,
        paypal_order_id=order_id,
        status=Payment.Status.COMPLETED,
    ).exists()
    if already_completed:
        return JsonResponse(
            {"status": "already_completed", "message": "This order was already processed."},
            status=200,
        )

    # --- Create pending payment record ---
    payment = Payment.objects.create(
        user=request.user,
        plan=plan,
        amount=amount,
        currency=currency,
        gateway=Payment.Gateway.PAYPAL,
        status=Payment.Status.PENDING,
        paypal_order_id=order_id,
    )

    logger.info(
        "PayPal order recorded: user=%s order=%s plan=%s amount=%s %s",
        request.user.email, order_id, plan, amount, currency,
    )

    return JsonResponse(
        {"status": "pending", "payment_id": payment.id},
        status=201,
    )


@login_required
@csrf_exempt
@require_POST
def paypal_capture_order(request):
    """
    POST /payment/paypal/capture-order/

    Receives capture details from the client-side actions.order.capture() call.
    Verifies the capture was successful, marks payment complete, and
    activates the subscription.

    This is the PRIMARY payment confirmation path.
    The webhook is the BACKUP for when this fails (user closes browser).

    Request body (JSON):
        order_id:       PayPal order ID
        capture_id:     Capture ID from capture response
        payer_id:       Payer ID
        payer_email:    Payer email
        status:         Capture status (must be "COMPLETED")
        amount:         Captured amount

    Returns:
        200: {"status": "success", ...}
        400: Invalid capture or missing data
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    order_id = data.get("order_id", "").strip()
    capture_id = data.get("capture_id", "").strip()
    payer_id = data.get("payer_id", "").strip()
    payer_email = data.get("payer_email", "").strip()
    status = data.get("status", "").strip()
    amount = data.get("amount", "0")

    if not order_id:
        return JsonResponse({"error": "order_id is required"}, status=400)

    # --- Find the pending payment ---
    try:
        payment = Payment.objects.get(
            user=request.user,
            paypal_order_id=order_id,
            gateway=Payment.Gateway.PAYPAL,
        )
    except Payment.DoesNotExist:
        return JsonResponse(
            {"error": "Payment not found. Create the order first via /payment/paypal/create-order/"},
            status=404,
        )

    # --- Already completed check ---
    if payment.status == Payment.Status.COMPLETED:
        logger.info("PayPal capture_order: already completed for order %s", order_id)
        subscription = getattr(request.user, "subscription", None)
        return JsonResponse({
            "status": "already_completed",
            "message": "This payment was already processed.",
            "subscription_active": subscription.is_active if subscription else False,
        })

    # --- Verify capture status ---
    if status != "COMPLETED":
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=["status", "updated_at"])
        logger.warning("PayPal capture failed for order %s: status=%s", order_id, status)
        return JsonResponse(
            {"error": f"Capture not completed. Status: {status}"},
            status=400,
        )

    # --- SERVER-SIDE AMOUNT VALIDATION ---
    # NEVER trust client-sent amount. The Payment record's amount was set
    # during create-order against PLAN_PRICES_USD — that's our source of truth.
    # If someone tampered with the JS to send amount: "0.01", we reject it.
    is_valid, expected_amount = _validate_payment_amount(
        payment.plan, payment.amount, payment.currency,
    )
    if not is_valid:
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=["status", "updated_at"])
        logger.critical(
            "PayPal amount tampering detected: user=%s order=%s "
            "plan=%s stored_amount=%s expected=%s client_sent=%s",
            request.user.email, order_id,
            payment.plan, payment.amount, expected_amount, amount,
        )
        return JsonResponse(
            {"error": "Payment verification failed (amount mismatch)"}, status=400,
        )

    # --- Mark payment completed ---
    payment.status = Payment.Status.COMPLETED
    payment.paypal_capture_id = capture_id
    payment.paypal_payer_id = payer_id
    payment.paypal_payer_email = payer_email
    payment.save(update_fields=[
        "status", "paypal_capture_id", "paypal_payer_id",
        "paypal_payer_email", "updated_at",
    ])

    logger.info(
        "PayPal payment completed: user=%s order=%s capture=%s plan=%s",
        request.user.email, order_id, capture_id, payment.plan,
    )

    # --- Activate subscription ---
    subscription = _activate_subscription(request.user, payment)

    return JsonResponse({
        "status": "success",
        "plan": payment.plan,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "subscription_active": subscription.is_active,
        "period_end": subscription.current_period_end.isoformat(),
    })


@csrf_exempt
@require_POST
def paypal_webhook(request):
    """
    POST /payment/paypal/webhook/

    Server-to-server backup notification from PayPal.
    If the user closes the browser before the JS callback fires,
    PayPal still sends this webhook.

    Events handled:
    - PAYMENT.CAPTURE.COMPLETED → activate subscription
    - PAYMENT.CAPTURE.DENIED   → mark payment failed
    - PAYMENT.CAPTURE.REFUNDED  → mark payment refunded

    Signature verification is enforced in production only.
    """
    # --- Verify webhook signature (production only) ---
    paypal_mode = getattr(settings, "PAYPAL_MODE", "sandbox")
    if paypal_mode == "live":
        from .paypal_utils import get_paypal_api
        api = get_paypal_api()

        body = request.body
        if isinstance(body, bytes):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                body = {}

        if not api.verify_webhook_signature(request.headers, body):
            logger.warning("PayPal webhook signature verification failed")
            return JsonResponse({"error": "Invalid webhook signature"}, status=400)

    # --- Parse webhook event ---
    try:
        event = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    event_type = event.get("event_type", "")
    resource = event.get("resource", {})

    logger.info("PayPal webhook received: event_type=%s", event_type)

    # --- Handle CAPTURE.COMPLETED ---
    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        capture_id = resource.get("id", "")
        order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id", "")
        amount = resource.get("amount", {}).get("value", "0")
        payer_email = resource.get("payer", {}).get("email_address", "")

        if not order_id and not capture_id:
            logger.warning("PayPal webhook: missing order_id and capture_id")
            return JsonResponse({"error": "Missing identifiers"}, status=400)

        # Find payment by order_id or capture_id
        payment = None
        if order_id:
            payment = Payment.objects.filter(paypal_order_id=order_id).first()
        if not payment and capture_id:
            payment = Payment.objects.filter(paypal_capture_id=capture_id).first()

        if not payment:
            # No matching payment record — might be from a different system
            logger.info("PayPal webhook: no matching payment for order=%s capture=%s", order_id, capture_id)
            return JsonResponse({"status": "ignored", "reason": "no matching payment"})

        if payment.status == Payment.Status.COMPLETED:
            logger.info("PayPal webhook: already completed for %s", order_id)
            return JsonResponse({"status": "already_completed"})

        # Mark completed
        payment.status = Payment.Status.COMPLETED
        payment.paypal_capture_id = capture_id or payment.paypal_capture_id
        payment.paypal_payer_email = payer_email or payment.paypal_payer_email
        if amount:
            payment.amount = Decimal(str(amount))
        payment.save(update_fields=[
            "status", "paypal_capture_id", "paypal_payer_email",
            "amount", "updated_at",
        ])

        # Activate subscription (idempotent)
        _activate_subscription(payment.user, payment)

        logger.info("PayPal webhook: activated subscription for %s via order %s", payment.user.email, order_id)

    # --- Handle CAPTURE.DENIED ---
    elif event_type == "PAYMENT.CAPTURE.DENIED":
        capture_id = resource.get("id", "")
        payment = Payment.objects.filter(paypal_capture_id=capture_id).first()
        if payment and payment.status != Payment.Status.COMPLETED:
            payment.status = Payment.Status.FAILED
            payment.save(update_fields=["status", "updated_at"])
            logger.info("PayPal webhook: marked payment failed for capture %s", capture_id)

    # --- Handle CAPTURE.REFUNDED ---
    elif event_type == "PAYMENT.CAPTURE.REFUNDED":
        capture_id = resource.get("id", "")
        payment = Payment.objects.filter(paypal_capture_id=capture_id).first()
        if payment:
            payment.status = Payment.Status.REFUNDED
            payment.save(update_fields=["status", "updated_at"])

            # Cancel subscription
            subscription = getattr(payment.user, "subscription", None)
            if subscription and subscription.status == Subscription.Status.ACTIVE:
                subscription.status = Subscription.Status.CANCELLED
                subscription.save(update_fields=["status", "updated_at"])
                logger.info("PayPal webhook: cancelled subscription for %s (refunded)", payment.user.email)

    else:
        logger.info("PayPal webhook: unhandled event type %s", event_type)

    return JsonResponse({"status": "ok"})


# =============================================================================
# Razorpay Views
# =============================================================================

@login_required
@csrf_exempt
@require_POST
def razorpay_create_order(request):
    """
    POST /payment/razorpay/create-order/

    Creates a Razorpay order SERVER-SIDE via razorpay.Client.order.create().
    Amount is in PAISE (e.g. ₹699 = 69900 paise).

    Request body (JSON):
        plan: "starter" or "pro"

    Returns:
        201: {"order_id": "...", "amount": 69900, "key_id": "...", "currency": "INR"}
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    plan_key = data.get("plan", "").strip().lower()
    if plan_key not in [Subscription.Plan.PRO, Subscription.Plan.PRO_YEARLY]:
        return JsonResponse({"error": f"Invalid plan: {plan_key}"}, status=400)

    # Get INR price
    amount_inr = PLAN_PRICES_INR.get(plan_key)
    if not amount_inr:
        return JsonResponse({"error": "Plan not available for INR"}, status=400)

    # Amount in paise (Razorpay expects integer paise)
    amount_paise = int(amount_inr * 100)

    # --- Auto-expire stale pending records (>10 min old) ---
    stale_cutoff = timezone.now() - timedelta(minutes=10)
    Payment.objects.filter(
        user=request.user,
        status=Payment.Status.PENDING,
        gateway=Payment.Gateway.RAZORPAY,
        created_at__lt=stale_cutoff,
    ).update(status=Payment.Status.FAILED)

    # --- Pending dedup: block rapid retries within 10 minutes ---
    recent_pending = Payment.objects.filter(
        user=request.user,
        status=Payment.Status.PENDING,
        gateway=Payment.Gateway.RAZORPAY,
        created_at__gte=stale_cutoff,
    ).exists()
    if recent_pending:
        return JsonResponse(
            {"error": "You have a pending payment. Please wait before trying again."},
            status=409,
        )

    # --- Create Razorpay order server-side ---
    try:
        import razorpay

        client = razorpay.Client(auth=(
            getattr(settings, "RAZORPAY_KEY_ID", ""),
            getattr(settings, "RAZORPAY_KEY_SECRET", ""),
        ))

        razorpay_order = client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "payment_capture": 1,  # Auto-capture
        })

    except ImportError:
        logger.error("razorpay package not installed. Run: pip install razorpay")
        return JsonResponse(
            {"error": "Razorpay is not configured. Please install the razorpay package."},
            status=503,
        )
    except Exception as e:
        logger.error("Razorpay order creation failed: %s", e)
        return JsonResponse(
            {"error": "Failed to create payment order. Please try again."},
            status=500,
        )

    razorpay_order_id = razorpay_order.get("id", "")

    # --- Record pending payment ---
    payment = Payment.objects.create(
        user=request.user,
        plan=plan_key,
        amount=amount_inr,
        currency="INR",
        gateway=Payment.Gateway.RAZORPAY,
        status=Payment.Status.PENDING,
        razorpay_order_id=razorpay_order_id,
    )

    logger.info(
        "Razorpay order created: user=%s order=%s plan=%s amount=₹%s",
        request.user.email, razorpay_order_id, plan_key, amount_inr,
    )

    return JsonResponse({
        "order_id": razorpay_order_id,
        "amount": amount_paise,
        "key_id": getattr(settings, "RAZORPAY_KEY_ID", ""),
        "currency": "INR",
        "payment_id": payment.id,
    }, status=201)


@login_required
@csrf_exempt
@require_POST
def razorpay_verify(request):
    """
    POST /payment/razorpay/verify/

    Verifies Razorpay HMAC-SHA256 signature after widget checkout.
    If valid, marks payment complete and activates subscription.

    Request body (JSON):
        razorpay_order_id:   Razorpay order ID
        razorpay_payment_id: Razorpay payment ID
        razorpay_signature:  HMAC signature

    Returns:
        200: {"status": "success", ...}
        400: Signature mismatch
        404: Payment not found
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    razorpay_order_id = data.get("razorpay_order_id", "").strip()
    razorpay_payment_id = data.get("razorpay_payment_id", "").strip()
    razorpay_signature = data.get("razorpay_signature", "").strip()

    if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
        return JsonResponse({"error": "Missing required fields"}, status=400)

    # --- Verify HMAC signature ---
    key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    generated_signature = hmac.new(
        key_secret.encode(), message.encode(), hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(generated_signature, razorpay_signature):
        logger.warning(
            "Razorpay signature mismatch for order %s: expected=%s got=%s",
            razorpay_order_id, generated_signature, razorpay_signature,
        )
        return JsonResponse({"error": "Payment verification failed (invalid signature)"}, status=400)

    # --- Find payment record ---
    try:
        payment = Payment.objects.get(
            user=request.user,
            razorpay_order_id=razorpay_order_id,
            gateway=Payment.Gateway.RAZORPAY,
        )
    except Payment.DoesNotExist:
        return JsonResponse(
            {"error": "Payment not found. Create the order first via /payment/razorpay/create-order/"},
            status=404,
        )

    # --- Already completed check ---
    if payment.status == Payment.Status.COMPLETED:
        subscription = getattr(request.user, "subscription", None)
        return JsonResponse({
            "status": "already_completed",
            "message": "This payment was already processed.",
            "subscription_active": subscription.is_active if subscription else False,
        })

    # --- SERVER-SIDE AMOUNT VALIDATION ---
    # The amount is set by the backend in create-order, stored on the Payment
    # record, and verified against the canonical plan pricing dict.
    # This catches any amount tampering between create and verify.
    is_valid, expected_amount = _validate_payment_amount(
        payment.plan, payment.amount, payment.currency,
    )
    if not is_valid:
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=["status", "updated_at"])
        logger.critical(
            "Razorpay amount tampering detected: user=%s order=%s "
            "plan=%s stored_amount=%s expected=%s",
            request.user.email, razorpay_order_id,
            payment.plan, payment.amount, expected_amount,
        )
        return JsonResponse(
            {"error": "Payment verification failed (amount mismatch)"}, status=400,
        )

    # --- Mark payment completed ---
    payment.status = Payment.Status.COMPLETED
    payment.razorpay_payment_id = razorpay_payment_id
    payment.razorpay_signature = razorpay_signature
    payment.save(update_fields=[
        "status", "razorpay_payment_id", "razorpay_signature", "updated_at",
    ])

    logger.info(
        "Razorpay payment verified: user=%s order=%s payment=%s plan=%s",
        request.user.email, razorpay_order_id, razorpay_payment_id, payment.plan,
    )

    # --- Activate subscription ---
    subscription = _activate_subscription(request.user, payment)

    return JsonResponse({
        "status": "success",
        "plan": payment.plan,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "subscription_active": subscription.is_active,
        "period_end": subscription.current_period_end.isoformat(),
    })


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """
    POST /payment/razorpay/webhook/

    Server-to-server backup notification from Razorpay.
    Verifies X-Razorpay-Signature using HMAC-SHA256.

    Events handled:
    - payment.captured → activate subscription
    - order.paid       → activate subscription
    """
    # --- Verify webhook signature ---
    webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
    if not webhook_secret:
        logger.warning("RAZORPAY_WEBHOOK_SECRET not configured — skipping verification")
    else:
        expected_signature = hmac.new(
            webhook_secret.encode(),
            request.body,
            hashlib.sha256,
        ).hexdigest()

        received_signature = request.headers.get("X-Razorpay-Signature", "")
        if not hmac.compare_digest(expected_signature, received_signature):
            logger.warning("Razorpay webhook signature mismatch")
            return JsonResponse({"error": "Invalid webhook signature"}, status=400)

    # --- Parse webhook payload ---
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    event = payload.get("event", "")
    payload_entity = payload.get("payload", {})

    logger.info("Razorpay webhook received: event=%s", event)

    # --- Handle payment.captured ---
    if event == "payment.captured":
        payment_entity = payload_entity.get("payment", {}).get("entity", {})
        razorpay_order_id = payment_entity.get("order_id", "")
        razorpay_payment_id = payment_entity.get("id", "")

        if not razorpay_order_id:
            return JsonResponse({"error": "Missing order_id in webhook"}, status=400)

        payment = Payment.objects.filter(
            razorpay_order_id=razorpay_order_id,
        ).first()

        if not payment:
            logger.info("Razorpay webhook: no matching payment for order %s", razorpay_order_id)
            return JsonResponse({"status": "ignored", "reason": "no matching payment"})

        if payment.status == Payment.Status.COMPLETED:
            return JsonResponse({"status": "already_completed"})

        # Mark completed
        payment.status = Payment.Status.COMPLETED
        payment.razorpay_payment_id = razorpay_payment_id or payment.razorpay_payment_id
        payment.save(update_fields=["status", "razorpay_payment_id", "updated_at"])

        # Activate subscription (idempotent)
        _activate_subscription(payment.user, payment)

        logger.info("Razorpay webhook: activated subscription for %s", payment.user.email)

    # --- Handle order.paid ---
    elif event == "order.paid":
        order_entity = payload_entity.get("order", {}).get("entity", {})
        razorpay_order_id = order_entity.get("id", "")

        if razorpay_order_id:
            payment = Payment.objects.filter(
                razorpay_order_id=razorpay_order_id,
            ).first()
            if payment and payment.status != Payment.Status.COMPLETED:
                payment.status = Payment.Status.COMPLETED
                payment.save(update_fields=["status", "updated_at"])
                _activate_subscription(payment.user, payment)
                logger.info("Razorpay webhook (order.paid): activated for %s", payment.user.email)

    else:
        logger.info("Razorpay webhook: unhandled event %s", event)

    return JsonResponse({"status": "ok"})


# =============================================================================
# Payment Cancellation
# =============================================================================

@login_required
@csrf_exempt
@require_POST
def cancel_pending_payment(request):
    """
    POST /payment/cancel-pending/

    Marks any pending payment for the user as CANCELLED.
    Called by JS when user closes Razorpay widget or cancels PayPal popup.
    This immediately unblocks the user so they can retry.
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        data = {}

    gateway = data.get("gateway", "").upper()
    order_id = data.get("order_id", "").strip()

    # Build filter
    filters = {
        "user": request.user,
        "status": Payment.Status.PENDING,
    }
    if gateway in ("RAZORPAY", "PAYPAL") and order_id:
        # Cancel specific pending payment
        if gateway == "RAZORPAY":
            filters["razorpay_order_id"] = order_id
        else:
            filters["paypal_order_id"] = order_id

    updated = Payment.objects.filter(**filters).update(status=Payment.Status.FAILED)

    if updated:
        logger.info(
            "Payment cancelled by user: user=%s gateway=%s order=%s count=%d",
            request.user.email, gateway, order_id, updated,
        )

    return JsonResponse({"status": "cancelled", "count": updated})


# =============================================================================
# Subscription Activation Engine
# =============================================================================

def _activate_subscription(user, payment):
    """
    Activate or renew a user's subscription after successful payment.

    This is THE single function that both PayPal and Razorpay call.
    It is idempotent — safe to call multiple times for the same payment.

    Steps:
    1. Idempotency guard: check if this payment already activated the sub
    2. Calculate period (30 days for monthly)
    3. update_or_create Subscription
    4. Create Order record for billing history

    Args:
        user: Django User instance
        payment: Completed Payment instance

    Returns:
        Subscription instance
    """
    # --- Idempotency guard ---
    existing_sub = getattr(user, "subscription", None)
    if existing_sub:
        # Check if this specific payment already activated the subscription
        if existing_sub.paypal_order_id == payment.paypal_order_id and payment.paypal_order_id:
            logger.info("Subscription already activated by PayPal order %s", payment.paypal_order_id)
            return existing_sub
        if existing_sub.razorpay_order_id == payment.razorpay_order_id and payment.razorpay_order_id:
            logger.info("Subscription already activated by Razorpay order %s", payment.razorpay_order_id)
            return existing_sub

    # --- Calculate billing period ---
    now = timezone.now()
    period_end = now + timedelta(days=30)

    # --- Determine amount in USD for the subscription model ---
    if payment.currency == "INR":
        # Store the USD-equivalent for consistency with the Subscription model
        usd_amount = PLAN_PRICES_USD.get(payment.plan, Decimal("0.00"))
    else:
        usd_amount = payment.amount

    # --- Create or update subscription ---
    sub, created = Subscription.objects.update_or_create(
        user=user,
        defaults={
            "email": user.email,
            "plan": payment.plan,
            "status": Subscription.Status.ACTIVE,
            "current_period_start": now,
            "current_period_end": period_end,
            "paypal_order_id": payment.paypal_order_id or "",
            "razorpay_order_id": payment.razorpay_order_id or "",
        },
    )

    # --- Create order record for billing history ---
    from cortex.account.models import Order

    plan_display = sub.get_plan_display().split("(")[0].strip()  # "Pro" from "Pro (Yearly)"
    order = Order.objects.create(
        user=user,
        payment=payment,
        amount_usd=usd_amount,
        currency=payment.currency,
        status=Order.Status.PAID,
        item=f"{plan_display} Plan",
        order_type=Order.OrderType.PURCHASE,
    )

    # --- Email the receipt + PDF tax invoice (exactly once per purchase:
    #     the idempotency guard above returns early on repeat calls) ---
    from cortex.account.emails import send_invoice_email
    send_invoice_email(user, order, payment, sub)
    logger.info("Invoice email queued: user=%s order=INV-%05d", user.email, order.id)

    action = "created" if created else "renewed"
    logger.info(
        "Subscription %s: user=%s plan=%s period_end=%s",
        action, user.email, payment.plan, period_end,
    )

    return sub


# =============================================================================
# Payment Result Pages
# =============================================================================

@login_required
def payment_success(request):
    """GET /payment/success/ — shown after successful payment."""
    # Get latest payment for this user
    latest_payment = Payment.objects.filter(
        user=request.user,
        status=Payment.Status.COMPLETED,
    ).order_by("-created_at").first()

    context = {
        "payment": latest_payment,
    }
    return render(request, "cortex/payment/payment_success.html", context)


@login_required
def payment_cancel(request):
    """GET /payment/cancel/ — shown when user cancels payment."""
    return render(request, "cortex/payment/payment_cancel.html")


@login_required
def payment_failed(request):
    """GET /payment/failed/ — shown when payment fails."""
    return render(request, "cortex/payment/payment_failed.html")


# =============================================================================
# Subscription Status API
# =============================================================================

@login_required
@require_GET
def subscription_status(request):
    """
    GET /payment/subscription/status/

    Returns current subscription status as JSON.
    Used by the IDE and frontend to check subscription state.
    """
    subscription = getattr(request.user, "subscription", None)
    credit_balance = None

    if subscription:
        credit_balance = getattr(subscription, "credit_balance", None)

    return JsonResponse({
        "has_subscription": subscription is not None,
        "is_active": subscription.is_active if subscription else False,
        "plan": subscription.plan if subscription else None,
        "status": subscription.status if subscription else None,
        "period_end": subscription.current_period_end.isoformat() if subscription else None,
        "credits": {
            "total": credit_balance.total_credits if credit_balance else 0,
            "used": credit_balance.used_credits if credit_balance else 0,
            "remaining": credit_balance.remaining if credit_balance else 0,
        } if credit_balance else None,
    })
