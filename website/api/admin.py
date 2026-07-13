"""
Cortex API — Django Admin Configuration

Registers all models with custom admin views.
Admin URL is configurable via DJANGO_ADMIN_URL env var (default: ops/cortex-admin/).
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import CrashReport, Subscription, UsageLog, Release


# =============================================================================
# Subscription Admin
# =============================================================================


class UsageLogInline(admin.TabularInline):
    model = UsageLog
    extra = 0
    readonly_fields = [
        "model_id", "input_cache_hit", "input_cache_miss", "input_tokens",
        "output_tokens", "ocr_pages", "raw_cost_usd", "gross_cost_usd",
        "credits_consumed", "created_at",
    ]
    fields = [
        "model_id", "input_cache_hit", "input_cache_miss", "output_tokens",
        "ocr_pages", "raw_cost_usd", "gross_cost_usd", "credits_consumed", "created_at",
    ]
    ordering = ["-created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        "email", "plan_badge", "status_badge",
        "current_period_end", "license_short",
    ]
    list_filter = ["plan", "status", "created_at"]
    search_fields = ["email", "license_key", "stripe_customer_id"]
    readonly_fields = ["license_key", "created_at", "updated_at"]
    inlines = [UsageLogInline]
    list_per_page = 25

    fieldsets = (
        ("Subscriber", {
            "fields": ("user", "email"),
        }),
        ("Plan", {
            "fields": ("plan", "status", "license_key"),
        }),
        ("Billing", {
            "fields": ("stripe_customer_id", "stripe_subscription_id"),
            "classes": ("collapse",),
        }),
        ("Billing Period", {
            "fields": ("current_period_start", "current_period_end"),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    def plan_badge(self, obj):
        colors = {
            "free": "#71717a",
            "starter": "#4d8dff",
            "pro": "#e8c547",
        }
        color = colors.get(obj.plan, "#71717a")
        return format_html(
            '<span style="color: {}; font-weight: bold; font-size: 13px;">{}</span>',
            color, obj.get_plan_display(),
        )
    plan_badge.short_description = "Plan"

    def status_badge(self, obj):
        colors = {
            "active": "green",
            "past_due": "orange",
            "cancelled": "red",
            "expired": "gray",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, obj.get_status_display(),
        )
    status_badge.short_description = "Status"

    def license_short(self, obj):
        return obj.license_key[:20] + "…" if len(obj.license_key) > 20 else obj.license_key
    license_short.short_description = "License Key"


# =============================================================================
# CrashReport Admin
# =============================================================================

@admin.register(CrashReport)
class CrashReportAdmin(admin.ModelAdmin):
    list_display = ["short_error", "ide_version", "os_version", "device_short", "created_at"]
    list_filter = ["error_type", "ide_version", "created_at"]
    search_fields = ["error_type", "error_message", "device_hash"]
    readonly_fields = [
        "device_hash", "ide_version", "os_version", "error_type",
        "error_message", "stack_trace", "context", "created_at",
    ]
    list_per_page = 50

    def short_error(self, obj):
        text = obj.error_type[:50]
        if len(obj.error_type) > 50:
            text += "…"
        return text
    short_error.short_description = "Error"

    def device_short(self, obj):
        return obj.device_hash[:12] + "…"
    device_short.short_description = "Device"

    def has_add_permission(self, request):
        """Crash reports are created by the API, not manually."""
        return False

    def has_change_permission(self, request, obj=None):
        """Crash reports are read-only in admin."""
        return False


# =============================================================================
# Release Admin
# =============================================================================


@admin.register(Release)
class ReleaseAdmin(admin.ModelAdmin):
    list_display = ["version", "force_badge", "file_size_display", "downloads_count", "is_active", "created_at"]
    list_filter = ["is_active", "force_update", "created_at"]
    search_fields = ["version", "release_notes"]
    readonly_fields = ["file_size", "sha256", "downloads_count", "created_at"]
    list_per_page = 20

    fieldsets = (
        ("Release Info", {
            "fields": ("version", "release_notes", "force_update", "is_active"),
        }),
        ("Binary File", {
            "fields": ("file", "file_size", "sha256", "downloads_count"),
        }),
        ("Metadata", {
            "fields": ("created_at",),
        }),
    )

    def force_badge(self, obj):
        if obj.force_update:
            return format_html(
                '<span style="color: #ff6b6b; font-weight: bold;">⚠ FORCE</span>'
            )
        return format_html('<span style="color: #6b7280;">Normal</span>')
    force_badge.short_description = "Update Type"

    def file_size_display(self, obj):
        if obj.file_size:
            mb = obj.file_size / (1024 * 1024)
            return f"{mb:.2f} MB"
        return "—"
    file_size_display.short_description = "Size"


# =============================================================================
# Payment / ModelConfig / Download tracking
# (Bug history: these were never registered — invisible in Django admin.)
# =============================================================================

from .models import DownloadEvent, ModelConfig, Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "plan", "amount", "currency", "gateway", "status", "created_at"]
    list_filter = ["gateway", "status", "plan", "currency"]
    search_fields = ["user__email", "paypal_order_id", "razorpay_order_id"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at"]


@admin.register(ModelConfig)
class ModelConfigAdmin(admin.ModelAdmin):
    list_display = ["version", "min_ide_version", "is_active", "created_at"]
    list_filter = ["is_active"]
    ordering = ["-created_at"]


@admin.register(DownloadEvent)
class DownloadEventAdmin(admin.ModelAdmin):
    list_display = ["created_at", "user", "version", "ip_address"]
    list_filter = ["version"]
    search_fields = ["user__email", "user__username", "ip_address"]
    ordering = ["-created_at"]
    readonly_fields = ["user", "release", "version", "ip_address", "user_agent", "created_at"]

    def has_add_permission(self, request):
        return False  # events come from the download endpoint only
