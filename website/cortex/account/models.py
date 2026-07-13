"""
Cortex Account — User Models

Custom user model + session tracking + API keys + order history.

CRITICAL: AUTH_USER_MODEL in settings.py must point to this BEFORE
running any migrations. Changing it after data exists is extremely painful.
"""
import hashlib
import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


# =============================================================================
# Extended User
# =============================================================================

class User(AbstractUser):
    """
    Extended user model for Cortex.

    Adds avatar, display name, timezone, and marketing preferences
    on top of Django's built-in AbstractUser (which gives us username,
    email, password, is_staff, date_joined, etc).
    """

    avatar = models.ImageField(
        upload_to="avatars/",
        blank=True,
        null=True,
        help_text="User profile picture.",
    )
    display_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Public display name (falls back to full_name or username).",
    )
    timezone = models.CharField(
        max_length=50,
        default="UTC",
        help_text="User timezone for display (e.g. 'Asia/Dubai').",
    )
    marketing_opt_in = models.BooleanField(
        default=False,
        help_text="Opted in to product updates and marketing emails.",
    )
    email_verified = models.BooleanField(
        default=False,
        help_text="Whether the user's email has been verified.",
    )
    # ── IDE version tracking (X-Cortex-Version header on API requests) ──
    last_seen_version = models.CharField(
        max_length=20,
        blank=True,
        default="",
        db_index=True,
        help_text="App version this user's IDE last reported.",
    )
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the IDE last talked to the API.",
    )

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return self.get_display_name_or_email()

    def get_display_name_or_email(self):
        """Return display_name, full_name, or email — whichever is available."""
        if self.display_name:
            return self.display_name
        full = self.get_full_name()
        if full:
            return full
        return self.email or self.username

    @property
    def initials(self):
        """Return 1-2 character initials for avatar placeholder."""
        name = self.get_full_name() or self.email or self.username
        parts = name.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return parts[0][0].upper() if parts else "?"

    @property
    def has_active_subscription(self):
        """Check if user has an active paid subscription."""
        return hasattr(self, "subscription") and self.subscription.is_active


# =============================================================================
# Active Session Tracking
# =============================================================================

class ActiveSession(models.Model):
    """
    Track active sessions across IDE, Web, CLI, JetBrains.

    Each login or API key usage creates a session record.
    Users can view and revoke sessions from the Security page.
    """

    class ClientType(models.TextChoices):
        WEB = "web", "Web Browser"
        IDE = "ide", "Cortex IDE"
        CLI = "cli", "Cortex CLI"
        JETBRAINS = "jetbrains", "JetBrains Plugin"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    client_type = models.CharField(
        max_length=20,
        choices=ClientType.choices,
        help_text="Which client created this session.",
    )
    ip_address = models.GenericIPAddressField(
        help_text="IP address of the client.",
    )
    location = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Geolocation string (e.g. 'Dubai, UAE').",
    )
    user_agent = models.TextField(
        blank=True,
        default="",
        help_text="Full User-Agent string.",
    )
    is_current = models.BooleanField(
        default=False,
        help_text="True if this is the session the user is viewing from.",
    )
    last_active = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_current", "-last_active"]
        verbose_name = "Active Session"
        verbose_name_plural = "Active Sessions"

    def __str__(self):
        current = " (current)" if self.is_current else ""
        return f"{self.user} — {self.get_client_type_display()}{current}"


# =============================================================================
# API Keys
# =============================================================================

class ApiKey(models.Model):
    """
    User API keys for programmatic access to Cortex API.

    Keys are hashed (SHA-256) — the raw key is only shown ONCE at creation.
    Users manage keys from the Integrations page.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(
        max_length=100,
        help_text="Human-readable label (e.g. 'My IDE key').",
    )
    key_hash = models.CharField(
        max_length=64,
        unique=True,
        help_text="SHA-256 hash of the raw API key.",
    )
    key_prefix = models.CharField(
        max_length=12,
        help_text="First 8 chars of key for display (e.g. 'cx_live_abc').",
    )
    scopes = models.JSONField(
        default=list,
        blank=True,
        help_text='Permission scopes (e.g. ["chat", "models"]).',
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "API Key"
        verbose_name_plural = "API Keys"

    def __str__(self):
        return f"{self.name} ({self.key_prefix}...)"

    @classmethod
    def generate_key(cls):
        """
        Generate a new API key and return (raw_key, key_hash, key_prefix).
        The raw key is shown to the user ONCE and never stored.
        """
        raw_key = f"cx_live_{secrets.token_hex(24)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_prefix = raw_key[:12]
        return raw_key, key_hash, key_prefix


# =============================================================================
# Order History
# =============================================================================

class Order(models.Model):
    """
    Billing order history — tracks all payments, renewals, and cancellations.

    Displayed on the Plan & Billing page.
    Stripe integration will populate stripe_payment_intent_id.
    """

    class Status(models.TextChoices):
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"
        PENDING = "pending", "Pending"

    class OrderType(models.TextChoices):
        PURCHASE = "purchase", "Purchase"
        RENEWAL = "renewal", "Renewal"
        UPGRADE = "upgrade", "Upgrade"
        CREDIT_PACK = "credit_pack", "Credit Pack"
        CANCELLATION = "cancellation", "Cancellation"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    payment = models.ForeignKey(
        "api.Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        help_text="Linked payment record for invoice details.",
    )
    stripe_payment_intent_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Stripe payment intent ID.",
    )
    amount_usd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount charged in USD.",
    )
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    item = models.CharField(
        max_length=100,
        help_text="What was purchased (e.g. 'Pro Plan').",
    )
    order_type = models.CharField(
        max_length=20,
        choices=OrderType.choices,
        help_text="Type of order (purchase, renewal, upgrade, etc).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Order"
        verbose_name_plural = "Orders"

    def __str__(self):
        return f"{self.item} — ${self.amount_usd} ({self.get_status_display()})"
