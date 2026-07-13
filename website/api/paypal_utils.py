"""
Cortex PayPal — REST API v2 Utility Class

Wraps PayPal's OAuth2 + Orders + Webhook verification APIs.
Used by payment_views.py for order creation, capture, and webhook handling.

Flow:
  1. Client-side JS SDK creates order → server records paypal_order_id
  2. Client-side SDK captures order → server verifies capture details
  3. Webhooks: server-to-server backup if JS callback fails
"""
import base64
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger("api")


class PayPalAPI:
    """
    PayPal REST API v2 client.

    Handles OAuth2 token management, order operations, and webhook verification.
    """

    def __init__(self):
        self.client_id = getattr(settings, "PAYPAL_CLIENT_ID", "")
        self.client_secret = getattr(settings, "PAYPAL_CLIENT_SECRET", "")
        self.mode = getattr(settings, "PAYPAL_MODE", "sandbox")
        self.webhook_id = getattr(settings, "PAYPAL_WEBHOOK_ID", "")
        self.base_url = getattr(settings, "PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")

        # OAuth token cache
        self._access_token = None
        self._token_expires_at = 0

    # ------------------------------------------------------------------
    # OAuth2 Token
    # ------------------------------------------------------------------

    def get_access_token(self):
        """
        Get or refresh OAuth2 access token (client_credentials flow).
        Caches token until 5 minutes before expiry.
        """
        now = time.time()
        if self._access_token and now < self._token_expires_at - 300:
            return self._access_token

        url = f"{self.base_url}/v1/oauth2/token"
        auth = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        try:
            resp = requests.post(
                url, headers=headers, data={"grant_type": "client_credentials"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            self._access_token = data["access_token"]
            self._token_expires_at = now + data.get("expires_in", 3600)

            logger.info("PayPal OAuth token obtained (expires in %ds)", data.get("expires_in", 3600))
            return self._access_token

        except requests.RequestException as e:
            logger.error("PayPal OAuth token request failed: %s", e)
            raise

    def _auth_headers(self, content_type="application/json"):
        """Build headers with fresh OAuth token."""
        return {
            "Authorization": f"Bearer {self.get_access_token()}",
            "Content-Type": content_type,
        }

    # ------------------------------------------------------------------
    # Order Operations
    # ------------------------------------------------------------------

    def create_order(self, amount, currency="USD", plan_id=None, return_url=None, cancel_url=None):
        """
        Create a PayPal order server-side (for reference/fallback).
        In normal flow, the JS SDK creates orders client-side and the server
        just records the paypal_order_id.

        Args:
            amount: Decimal amount (e.g. Decimal('10.00'))
            currency: USD or INR
            plan_id: Optional PayPal plan ID for subscriptions
            return_url: Redirect URL after successful payment
            cancel_url: Redirect URL if user cancels

        Returns:
            dict with 'id' (order ID) and 'status'
        """
        url = f"{self.base_url}/v2/checkout/orders"

        payload = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {
                    "currency_code": currency,
                    "value": str(amount),
                },
                "description": f"Cortex Subscription — ${amount} {currency}",
            }],
        }

        if return_url and cancel_url:
            payload["application_context"] = {
                "return_url": return_url,
                "cancel_url": cancel_url,
                "brand_name": "Cortex AI IDE",
                "user_action": "PAY_NOW",
            }

        try:
            resp = requests.post(
                url, headers=self._auth_headers(), json=payload, timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            logger.info("PayPal order created: %s (status: %s)", data.get("id"), data.get("status"))
            return data

        except requests.RequestException as e:
            logger.error("PayPal create_order failed: %s", e)
            if hasattr(e, "response") and e.response is not None:
                logger.error("PayPal response: %s", e.response.text)
            raise

    def capture_order(self, order_id):
        """
        Capture an approved PayPal order.

        Args:
            order_id: PayPal order ID (e.g. "5O190127TN364715T")

        Returns:
            dict with capture details including capture_id, status, payer info
        """
        url = f"{self.base_url}/v2/checkout/orders/{order_id}/capture"

        try:
            resp = requests.post(
                url, headers=self._auth_headers(), json={}, timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            logger.info("PayPal order captured: %s", order_id)
            return data

        except requests.RequestException as e:
            logger.error("PayPal capture_order failed for %s: %s", order_id, e)
            if hasattr(e, "response") and e.response is not None:
                logger.error("PayPal response: %s", e.response.text)
            raise

    def get_order_details(self, order_id):
        """
        Fetch order details (for verification fallback).

        Args:
            order_id: PayPal order ID

        Returns:
            dict with full order details
        """
        url = f"{self.base_url}/v2/checkout/orders/{order_id}"

        try:
            resp = requests.get(
                url, headers=self._auth_headers(), timeout=15,
            )
            resp.raise_for_status()
            return resp.json()

        except requests.RequestException as e:
            logger.error("PayPal get_order_details failed for %s: %s", order_id, e)
            raise

    # ------------------------------------------------------------------
    # Webhook Verification
    # ------------------------------------------------------------------

    def verify_webhook_signature(self, headers, body):
        """
        Verify PayPal webhook signature using PayPal's official verification endpoint.

        Args:
            headers: dict of request headers (need PayPal-AUTH-ALGO, CERT-URL, etc.)
            body: Raw request body (bytes or str)

        Returns:
            True if signature is valid, False otherwise
        """
        if self.mode != "live" or not self.webhook_id:
            # Skip verification in sandbox mode
            logger.info("PayPal webhook verification skipped (sandbox mode)")
            return True

        url = f"{self.base_url}/v1/notifications/verify-webhook-signature"

        payload = {
            "auth_algo": headers.get("paypal-auth-algo", ""),
            "cert_url": headers.get("paypal-cert-url", ""),
            "transmission_id": headers.get("paypal-transmission-id", ""),
            "transmission_sig": headers.get("paypal-transmission-sig", ""),
            "transmission_time": headers.get("paypal-transmission-time", ""),
            "webhook_id": self.webhook_id,
            "webhook_event": body if isinstance(body, dict) else {},
        }

        try:
            resp = requests.post(
                url, headers=self._auth_headers(), json=payload, timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            verification_status = data.get("verification_status")
            if verification_status == "SUCCESS":
                logger.info("PayPal webhook signature verified successfully")
                return True
            else:
                logger.warning("PayPal webhook verification failed: %s", verification_status)
                return False

        except requests.RequestException as e:
            logger.error("PayPal webhook verification request failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_sandbox(self):
        """Check if running in sandbox mode."""
        return self.mode == "sandbox"

    def get_client_id(self):
        """Return the PayPal client ID (for frontend JS SDK)."""
        return self.client_id


def get_paypal_api():
    """Factory function to get a PayPalAPI instance."""
    return PayPalAPI()
