"""
Cortex Payment — URL Routing

PayPal:
/payment/paypal/create-order/   -> Record client-side PayPal order (POST)
/payment/paypal/capture-order/  -> Verify capture + activate subscription (POST)
/payment/paypal/webhook/        -> Backup: PayPal server-to-server notification (POST)

Razorpay:
/payment/razorpay/create-order/ -> Create Razorpay order server-side (POST)
/payment/razorpay/verify/       -> Verify HMAC signature + activate subscription (POST)
/payment/razorpay/webhook/      -> Backup: Razorpay server-to-server notification (POST)

Result Pages:
/payment/success/               -> Success page after payment (GET)
/payment/cancel/                -> Cancel page if user bailed (GET)
/payment/failed/                -> Failed page on error (GET)

API:
/payment/subscription/status/   -> JSON subscription status (GET)
"""
from django.urls import path

from . import payment_views

app_name = "payment"

urlpatterns = [
    # ---- PayPal ----
    path(
        "paypal/create-order/",
        payment_views.paypal_create_order,
        name="paypal-create-order",
    ),
    path(
        "paypal/capture-order/",
        payment_views.paypal_capture_order,
        name="paypal-capture-order",
    ),
    path(
        "paypal/webhook/",
        payment_views.paypal_webhook,
        name="paypal-webhook",
    ),

    # ---- Razorpay ----
    path(
        "razorpay/create-order/",
        payment_views.razorpay_create_order,
        name="razorpay-create-order",
    ),
    path(
        "razorpay/verify/",
        payment_views.razorpay_verify,
        name="razorpay-verify",
    ),
    path(
        "razorpay/webhook/",
        payment_views.razorpay_webhook,
        name="razorpay-webhook",
    ),

    # ---- Result Pages ----
    path("success/", payment_views.payment_success, name="success"),
    path("cancel/", payment_views.payment_cancel, name="cancel"),
    path("failed/", payment_views.payment_failed, name="failed"),

    # ---- Cancel Pending ----
    path("cancel-pending/", payment_views.cancel_pending_payment, name="cancel-pending"),

    # ---- Subscription Status API ----
    path(
        "subscription/status/",
        payment_views.subscription_status,
        name="subscription-status",
    ),
]
