"""
Cortex API — Database Models

Models that power the BYOK subscription architecture:
1. ModelConfig     — remote model configuration served to the IDE
2. Subscription    — user subscription plans (Pro $10/month)
3. CrashReport     — opt-in crash reports from the IDE
4. Payment         — PayPal + Razorpay payment records

All LLM inference is BYOK — users pay providers directly.
The subscription covers platform services only:
  - Web search (SerpAPI)
  - Semantic search / embeddings (SiliconFlow)
  - OCR (Mistral AI)
"""
import hashlib
import json
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


# =============================================================================
# Model Config
# =============================================================================

class ModelConfig(models.Model):
    """
    Remote model configuration served to the IDE via GET /api/v1/models/config/

    The IDE fetches this on startup. It caches locally and only refetches
    when the version changes (ETag support).

    Each model in the config has an ``access`` field:
      - ``included`` — model uses Cortex subscription credits (DeepSeek, MiMo)
      - ``byok``     — model requires user's own API key (OpenAI, Qwen, OpenRouter)

    Only ONE config should be active at a time.
    """
    version = models.CharField(
        max_length=50,
        unique=True,
        help_text="Config version (e.g. '2026.06.1'). IDE compares to local cache.",
    )
    config_json = models.JSONField(
        help_text="Full model config JSON — providers, models, defaults, failover order.",
    )
    min_ide_version = models.CharField(
        max_length=20,
        default="0.9.0",
        help_text="Minimum IDE version required to use this config.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Only the active config is served to IDEs.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Model Configuration"
        verbose_name_plural = "Model Configurations"

    def __str__(self):
        status = "ACTIVE" if self.is_active else "inactive"
        return f"Config v{self.version} ({status})"

    def to_api_response(self):
        """
        Format as the JSON response the IDE expects.
        Adds metadata fields (version, min_supported_ide_version, last_updated).
        """
        data = dict(self.config_json)  # shallow copy
        data["version"] = self.version
        data["min_supported_ide_version"] = self.min_ide_version
        data["last_updated"] = self.created_at.isoformat()
        return data

    @classmethod
    def get_active(cls):
        """Return the currently active config, or None."""
        return cls.objects.filter(is_active=True).first()

    def save(self, *args, **kwargs):
        """If this config is being activated, deactivate all others."""
        if self.is_active:
            ModelConfig.objects.filter(is_active=True).exclude(pk=self.pk).update(
                is_active=False
            )
        super().save(*args, **kwargs)


# =============================================================================
# Subscription Plans
# =============================================================================

class Subscription(models.Model):
    """
    User subscription — controls access to included models (DeepSeek/MiMo).

    Plans:
      - Pro ($10/mo): generous credits, DeepSeek + MiMo + Mistral (OCR) + SiliconFlow (embeddings),
        web search, basic security, early access
      - Pro Yearly ($80/yr): same features, 33% savings

    BYOK models (OpenAI, Qwen, Kimi, OpenRouter) do NOT require a subscription.
    """

    class Plan(models.TextChoices):
        PRO = "pro", "Pro"  # $10/mo (899 INR) - OCR, Web Search, Embeddings
        PRO_YEARLY = "pro_yearly", "Pro (Yearly)"  # $80/yr (6999 INR)

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past Due"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    # Unique subscription ID (used as license token for IDE auth)
    license_key = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Unique license key the IDE uses to validate subscription.",
    )
    # Link to Django user (optional — can also be email-only)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="subscription",
    )
    email = models.EmailField(
        db_index=True,
        help_text="Subscriber email (used for magic link sign-in).",
    )
    plan = models.CharField(
        max_length=20,
        choices=Plan.choices,
        default=Plan.PRO,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    # Billing
    # Payment gateway IDs (Stripe, PayPal, Razorpay)
    stripe_customer_id = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Stripe customer ID (if using Stripe billing).",
    )
    stripe_subscription_id = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Stripe subscription ID (if using Stripe billing).",
    )
    paypal_order_id = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Last PayPal order ID that activated this subscription.",
    )
    razorpay_order_id = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Last Razorpay order ID that activated this subscription.",
    )
    # Dates
    current_period_start = models.DateTimeField(
        help_text="Start of current billing period.",
    )
    current_period_end = models.DateTimeField(
        help_text="End of current billing period (credits reset after this).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Subscription"
        verbose_name_plural = "Subscriptions"

    def __str__(self):
        return f"{self.email} — {self.get_plan_display()} ({self.status})"

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE and self.current_period_end > timezone.now()

    @property
    def monthly_credits(self):
        """
        Return credit allocation for this plan.

        Pro plan: $10/mo (899 INR) - includes OCR, Web Search, Embeddings
        Pro Yearly: $80/yr (6999 INR) - same features, 33% savings
        No credit system needed - all LLM usage is BYOK.
        """
        allocations = {
            self.Plan.PRO: 0,  # No credits - BYOK only
            self.Plan.PRO_YEARLY: 0,  # No credits - BYOK only
        }
        return allocations.get(self.plan, 0)

    def save(self, *args, **kwargs):
        """Auto-generate the license key on first save.

        Bug history: nothing ever called generate_license_key() —
        _activate_subscription's update_or_create inserted license_key=''
        (CharField default), so the first real subscription had an EMPTY
        key (IDE license validation impossible) and the SECOND customer's
        activation crashed on the unique constraint (two '' keys).
        """
        if not self.license_key:
            self.license_key = self.generate_license_key()
        super().save(*args, **kwargs)

    @classmethod
    def generate_license_key(cls):
        """Generate a unique license key."""
        return f"cortex_{secrets.token_hex(28)}"

    @classmethod
    def validate_license(cls, license_key):
        """
        Validate a license key and return the subscription if valid.
        Returns (subscription, error_message) tuple.
        """
        if not license_key:
            return None, "No license key provided."
        try:
            sub = cls.objects.get(license_key=license_key)
        except cls.DoesNotExist:
            return None, "Invalid license key."
        if not sub.is_active:
            return None, f"Subscription is {sub.get_status_display().lower()}."
        return sub, None


# =============================================================================
# =============================================================================
# Usage Log (per-request metering)
# =============================================================================

class UsageLog(models.Model):
    """
    Per-request usage log for subscription services (OCR, embeddings, web search).
    LLM usage (BYOK) is NOT logged here — tracked locally on desktop.
    """

    class ModelId(models.TextChoices):
        # DeepSeek (promo + base tiers)
        DEEPSEEK_V4_PRO_PROMO = "deepseek-v4-pro-promo", "DeepSeek V4 Pro (Promo)"
        DEEPSEEK_V4_PRO_BASE = "deepseek-v4-pro-base", "DeepSeek V4 Pro (Base)"
        # MiMo (Xiaomi)
        MIMO_V2_5_PRO = "mimo-v2.5-pro", "MiMo V2.5 Pro"
        MIMO_V2_5 = "mimo-v2.5", "MiMo V2.5"
        # Mistral (OCR / vision)
        MISTRAL_LARGE = "mistral-large-latest", "Mistral Large"
        # Fallback
        OTHER = "other", "Other"

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="usage_logs",
    )
    model_id = models.CharField(
        max_length=50,
        choices=ModelId.choices,
        help_text="Which included model was used.",
    )

    # Granular token metrics (from billing engine)
    input_cache_hit = models.PositiveIntegerField(
        default=0,
        help_text="Input tokens served from prompt cache.",
    )
    input_cache_miss = models.PositiveIntegerField(
        default=0,
        help_text="Input tokens NOT from prompt cache.",
    )
    output_tokens = models.PositiveIntegerField(
        default=0,
        help_text="Output tokens generated.",
    )
    ocr_pages = models.PositiveIntegerField(
        default=0,
        help_text="Number of images/pages processed via Mistral OCR.",
    )

    # Legacy field — kept for backward compat, auto-computed from above
    input_tokens = models.PositiveIntegerField(
        default=0,
        help_text="Total input tokens (cache_hit + cache_miss). Auto-populated.",
    )

    # Financial breakdown (from billing engine)
    raw_cost_usd = models.DecimalField(
        max_digits=12,
        decimal_places=8,
        default=0,
        help_text="Raw infrastructure cost in USD before markup.",
    )
    gross_cost_usd = models.DecimalField(
        max_digits=12,
        decimal_places=8,
        default=0,
        help_text="Cost in USD after 20% platform markup.",
    )
    credits_consumed = models.PositiveIntegerField(
        default=0,
        help_text="Wallet credit points deducted (= gross_cost_usd / $0.01).",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Usage Log"
        verbose_name_plural = "Usage Logs"

    def __str__(self):
        return (
            f"{self.subscription.email} — {self.model_id} — "
            f"{self.credits_consumed} pts @ {self.created_at:%Y-%m-%d %H:%M}"
        )

    def save(self, *args, **kwargs):
        """Auto-populate input_tokens from cache hit + miss — but ONLY when
        the granular cache fields are actually provided.

        Bug history (2026-07-13): this used to overwrite input_tokens
        unconditionally. The service proxies (_proxy_siliconflow_embeddings,
        _proxy_mistral_ocr) pass input_tokens directly and never set the
        cache fields, so every proxy row was clobbered to 0+0=0 on save —
        605 embedding rows this month alone recorded 0 tokens while
        SiliconFlow verifiably returned real usage.total_tokens on each
        call. The account usage page therefore showed '0 Embedding Tokens'
        for a heavy embeddings user."""
        if self.input_cache_hit or self.input_cache_miss:
            self.input_tokens = self.input_cache_hit + self.input_cache_miss
        super().save(*args, **kwargs)


# =============================================================================
# Crash Report
# =============================================================================

class CrashReport(models.Model):
    """
    Opt-in crash reports from the IDE via POST /api/v1/telemetry/crash/

    Privacy considerations:
    - device_hash is a one-way SHA-256 hash of the hardware ID (never raw PII)
    - stack_trace may contain user code — treat as sensitive
    - Telemetry is opt-in only (IDE setting controls this)
    """
    device_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text="SHA-256 hash of hardware ID (never store raw device IDs).",
    )
    ide_version = models.CharField(
        max_length=20,
        help_text="IDE version that crashed (e.g. '0.9.2').",
    )
    os_version = models.CharField(
        max_length=100,
        help_text="OS version (e.g. 'Windows 11 23H2').",
    )
    error_type = models.CharField(
        max_length=200,
        db_index=True,
        help_text="Error classification (e.g. 'QtWebEngine_AccessViolation').",
    )
    error_message = models.TextField(
        help_text="Human-readable error message.",
    )
    stack_trace = models.TextField(
        blank=True,
        default="",
        help_text="Stack trace if available. May contain user code — treat as sensitive.",
    )
    context = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="What the user was doing when the crash occurred.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Crash Report"
        verbose_name_plural = "Crash Reports"

    def __str__(self):
        return f"[{self.error_type}] v{self.ide_version} @ {self.created_at:%Y-%m-%d %H:%M}"

    @classmethod
    def cleanup_old(cls, days=90):
        """Delete crash reports older than N days. Call via cron or management command."""
        cutoff = timezone.now() - timezone.timedelta(days=days)
        deleted, _ = cls.objects.filter(created_at__lt=cutoff).delete()
        return deleted

    @classmethod
    def count_recent(cls, device_hash, hours=1):
        """Count crash reports from a device in the last N hours (rate limiting)."""
        cutoff = timezone.now() - timezone.timedelta(hours=hours)
        return cls.objects.filter(
            device_hash=device_hash,
            created_at__gte=cutoff,
        ).count()


# =============================================================================
# Payment — Unified for PayPal & Razorpay
# =============================================================================

class Payment(models.Model):
    """
    Unified payment record for both PayPal and Razorpay.

    Tracks every payment attempt with gateway-specific IDs and status.
    Used by:
    - PayPal flow: create_order (client-side) → record Payment(pending) →
      capture_order → mark completed → activate subscription
    - Razorpay flow: create_order (server-side) → record Payment(pending) →
      verify → mark completed → activate subscription
    - Webhooks: backup layer that activates if JS callback fails
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    class Gateway(models.TextChoices):
        PAYPAL = "paypal", "PayPal"
        RAZORPAY = "razorpay", "Razorpay"

    # Who paid and what they bought
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    plan = models.CharField(
        max_length=20,
        choices=Subscription.Plan.choices,
        help_text="Which plan was purchased.",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount charged.",
    )
    currency = models.CharField(
        max_length=3,
        default="USD",
        help_text="USD or INR.",
    )
    gateway = models.CharField(
        max_length=10,
        choices=Gateway.choices,
        help_text="Which payment gateway processed this.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # PayPal-specific fields
    paypal_order_id = models.CharField(
        max_length=100, blank=True, default="",
        help_text="PayPal v2 order ID (created client-side by JS SDK).",
    )
    paypal_capture_id = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Capture ID after successful PayPal capture.",
    )
    paypal_payer_id = models.CharField(
        max_length=100, blank=True, default="",
        help_text="PayPal payer ID.",
    )
    paypal_payer_email = models.CharField(
        max_length=200, blank=True, default="",
        help_text="PayPal payer email.",
    )

    # Razorpay-specific fields
    razorpay_order_id = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Razorpay order ID (created server-side).",
    )
    razorpay_payment_id = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Razorpay payment ID after success.",
    )
    razorpay_signature = models.CharField(
        max_length=200, blank=True, default="",
        help_text="HMAC signature for Razorpay verification.",
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Payment"
        verbose_name_plural = "Payments"

    def __str__(self):
        return (
            f"{self.user} — ${self.amount} {self.currency} "
            f"({self.get_gateway_display()}) [{self.get_status_display()}]"
        )

    @property
    def is_completed(self):
        return self.status == self.Status.COMPLETED

    @property
    def display_id(self):
        """Return the gateway-specific order ID."""
        if self.gateway == self.Gateway.PAYPAL:
            return self.paypal_order_id
        return self.razorpay_order_id


# =============================================================================
# Auth Token (Desktop OAuth2)
# =============================================================================

class AuthToken(models.Model):
    """
    OAuth2 tokens for Desktop IDE authentication.
    Created when user logs in via browser OAuth2 flow.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auth_tokens",
    )
    access_token = models.CharField(max_length=255, unique=True, db_index=True)
    refresh_token = models.CharField(max_length=255, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    device_info = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"AuthToken for {self.user.email} ({'active' if self.is_active else 'revoked'})"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @classmethod
    def create_for_user(cls, user, device_info=None):
        """Create a new token pair for a user."""
        import secrets
        access_token = f"cortex_at_{secrets.token_hex(32)}"
        refresh_token = f"cortex_rt_{secrets.token_hex(32)}"
        return cls.objects.create(
            user=user,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=timezone.now() + timezone.timedelta(days=30),
            device_info=device_info or {},
        )

    @classmethod
    def validate_access_token(cls, token):
        """Validate an access token and return the user, or None."""
        try:
            auth_token = cls.objects.select_related("user").get(
                access_token=token,
                is_active=True,
            )
            if auth_token.is_expired:
                return None
            return auth_token.user
        except cls.DoesNotExist:
            return None

    def refresh(self):
        """Refresh the access token using the refresh token."""
        import secrets
        self.access_token = f"cortex_at_{secrets.token_hex(32)}"
        self.expires_at = timezone.now() + timezone.timedelta(days=30)
        self.save(update_fields=["access_token", "expires_at", "last_used_at"])
        return self.access_token

    def revoke(self):
        """Revoke this token."""
        self.is_active = False
        self.save(update_fields=["is_active"])


# =============================================================================
# Release — Desktop IDE version releases
# =============================================================================


def _release_upload_path(instance, filename):
    """Store releases in media/releases/ with versioned filename."""
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "exe"
    return f"releases/Cortex_Setup_v{instance.version}.{ext}"


class Release(models.Model):
    """
    Desktop IDE release — uploaded via admin panel.
    
    Each release has a version, binary file, and a force_update flag.
    When force_update=True, the desktop IDE blocks usage until the user
    installs this version.
    """
    version = models.CharField(max_length=20, unique=True, db_index=True)
    release_notes = models.TextField(blank=True, default="")
    force_update = models.BooleanField(
        default=False,
        help_text="If checked, desktop IDE will BLOCK usage until this version is installed.",
    )
    file = models.FileField(upload_to=_release_upload_path, max_length=255)
    file_size = models.BigIntegerField(default=0, help_text="File size in bytes")
    sha256 = models.CharField(max_length=64, default="", help_text="SHA-256 checksum")
    downloads_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, help_text="Only active releases are served")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Release"
        verbose_name_plural = "Releases"

    def __str__(self):
        return f"v{self.version} {'(force)' if self.force_update else ''}"

    def save(self, *args, **kwargs):
        """Auto-populate file_size and sha256 on save if file is present."""
        if self.file:
            if not self.file_size and self.file.size > 0:
                self.file_size = self.file.size
            if not self.sha256 and self.file.size > 0:
                import hashlib
                sha = hashlib.sha256()
                self.file.open("rb")
                for chunk in iter(lambda: self.file.read(8192), b""):
                    sha.update(chunk)
                self.file.close()
                self.sha256 = sha.hexdigest()
        super().save(*args, **kwargs)

    @classmethod
    def latest_active(cls):
        """Return the latest active release, or None."""
        return cls.objects.filter(is_active=True).order_by("-created_at").first()

    @classmethod
    def latest_version(cls) -> str:
        """Return the version string of the latest active release, or '0.0.0'."""
        r = cls.latest_active()
        return r.version if r else "0.0.0"


class DownloadEvent(models.Model):
    """
    One row per installer download from /api/v1/download/latest/.

    Complements Release.downloads_count (aggregate) with WHO downloaded
    WHAT version WHEN — surfaced in the admin panel Downloads page.
    User is nullable: the download page allows anonymous downloads.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="download_events",
    )
    release = models.ForeignKey(
        Release,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="download_events",
    )
    version = models.CharField(max_length=20, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Download Event"
        verbose_name_plural = "Download Events"

    def __str__(self):
        who = self.user.email if self.user else (self.ip_address or "anonymous")
        return f"v{self.version} by {who} @ {self.created_at:%Y-%m-%d %H:%M}"
