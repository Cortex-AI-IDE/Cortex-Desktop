"""
Cortex Account — Django Admin Configuration

Registers the custom User model plus account-related models.
Bug history: this file didn't exist, so registered USERS never appeared
in Django admin at all (only api-app models were registered).
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import ActiveSession, ApiKey, Order, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Custom User admin — Django's UserAdmin + Cortex fields."""

    list_display = [
        "email", "username", "display_name", "email_verified",
        "last_seen_version", "last_seen_at", "is_active", "date_joined",
    ]
    list_filter = ["email_verified", "is_active", "is_superuser", "last_seen_version"]
    search_fields = ["email", "username", "display_name", "first_name", "last_name"]
    ordering = ["-date_joined"]
    readonly_fields = ["last_seen_version", "last_seen_at", "date_joined", "last_login"]

    fieldsets = BaseUserAdmin.fieldsets + (
        ("Cortex Profile", {
            "fields": (
                "display_name", "avatar", "timezone",
                "marketing_opt_in", "email_verified",
            ),
        }),
        ("IDE Telemetry", {
            "fields": ("last_seen_version", "last_seen_at"),
        }),
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "item", "amount_usd", "currency", "status", "order_type", "created_at"]
    list_filter = ["status", "order_type", "currency"]
    search_fields = ["user__email", "user__username", "item"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at"]


@admin.register(ActiveSession)
class ActiveSessionAdmin(admin.ModelAdmin):
    list_display = ["user", "client_type", "ip_address", "is_current", "created_at"]
    list_filter = ["client_type", "is_current"]
    search_fields = ["user__email", "ip_address"]
    ordering = ["-created_at"]


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ["user", "name", "created_at"]
    search_fields = ["user__email", "name"]
    ordering = ["-created_at"]
