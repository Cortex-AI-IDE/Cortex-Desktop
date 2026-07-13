"""
Cortex API — URL routing

Endpoints:
- GET  /api/v1/models/config/           — Model config for IDE
- POST /api/v1/subscription/validate/   — Validate subscription license
- GET  /api/v1/subscription/credits/    — Get credit balance
- POST /api/v1/proxy/chat/              — Proxy included-model requests (DeepSeek/MiMo/Mistral/SiliconFlow)
- POST /api/v1/telemetry/crash/         — Crash report ingestion

Auth endpoints (Desktop OAuth2):
- GET  /api/v1/auth/login/              — Get OAuth2 authorization URL
- GET  /api/v1/auth/authorize/          — Authorization endpoint (after browser login)
- POST /api/v1/auth/callback/           — Exchange auth code for tokens
- POST /api/v1/auth/login/credentials/  — Direct login with email+password
- POST /api/v1/auth/refresh/            — Refresh access token
- POST /api/v1/auth/logout/             — Revoke tokens
- GET  /api/v1/auth/me/                 — Get current user info

Profile/Usage/Billing endpoints (Desktop):
- GET  /api/v1/profile/                 — Get user profile
- PATCH /api/v1/profile/                — Update user profile
- GET  /api/v1/usage/summary/           — Get usage summary
- GET  /api/v1/usage/sync/              — Sync local usage to server
- GET  /api/v1/billing/subscription/    — Get current subscription
- GET  /api/v1/billing/credits/         — Get credit balance
- GET  /api/v1/billing/history/         — Get payment history
"""
from django.urls import path

from . import auth_views, views

app_name = "api"

urlpatterns = [
    # ── Existing endpoints ─────────────────────────────────────────────
    path("v1/subscription/validate/", views.subscription_validate_view, name="subscription-validate"),
    path("v1/subscription/credits/", views.subscription_credits_view, name="subscription-credits"),
    path("v1/proxy/chat/", views.proxy_chat_view, name="proxy-chat"),
    path("v1/telemetry/crash/", views.crash_report_view, name="crash-report"),

    # ── Auth endpoints (Desktop OAuth2) ────────────────────────────────
    path("v1/auth/login/", auth_views.auth_login_view, name="auth-login"),
    path("v1/auth/authorize/", auth_views.auth_authorize_view, name="auth-authorize"),
    path("v1/auth/callback/", auth_views.auth_callback_view, name="auth-callback"),
    path("v1/auth/login/credentials/", auth_views.auth_login_credentials_view, name="auth-login-credentials"),
    path("v1/auth/refresh/", auth_views.auth_refresh_view, name="auth-refresh"),
    path("v1/auth/logout/", auth_views.auth_logout_view, name="auth-logout"),
    path("v1/auth/me/", auth_views.auth_me_view, name="auth-me"),

    # ── Profile endpoints ─────────────────────────────────────────────
    path("v1/profile/", views.profile_view, name="profile"),

    # ── Usage endpoints ───────────────────────────────────────────────
    path("v1/usage/summary/", views.usage_summary_view, name="usage-summary"),
    path("v1/usage/sync/", views.usage_sync_view, name="usage-sync"),

    # ── Billing endpoints ─────────────────────────────────────────────
    path("v1/billing/subscription/", views.billing_subscription_view, name="billing-subscription"),
    path("v1/billing/credits/", views.billing_credits_view, name="billing-credits"),
    path("v1/billing/history/", views.billing_history_view, name="billing-history"),

    # ── Download & Version endpoints ───────────────────────────────────
    path("v1/download/version/", views.download_version_view, name="download-version"),
    path("v1/download/latest/", views.download_latest_view, name="download-latest"),
    path("v1/version/check/", views.version_check_view, name="version-check"),
]
