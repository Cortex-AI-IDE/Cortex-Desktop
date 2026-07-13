"""
Root URL configuration for Cortex server.

Routes:
/                           → Marketing landing page (cortex app)
/download/                  → Download page
/privacy/                   → Privacy policy
/terms/                     → Terms of service
/license/                   → EULA

/api/v1/models/config/      → Model config API (IDE fetches this)
/api/v1/telemetry/crash/    → Crash report ingestion

/ops/cortex-admin/          → Django admin (custom URL, not /admin/)
/ops/health/                → Health check endpoint
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from api.views import health_check_view

urlpatterns = [
    # Marketing site (cortex app)
    path("", include("cortex.urls")),

    # Account panel (user auth, profile, usage, billing)
    path("account/", include("cortex.account.urls")),

    # Social auth — Google + GitHub OAuth2
    path("auth/", include("social_django.urls", namespace="social")),

    # API endpoints (IDE ↔ Server)
    path("api/", include("api.urls")),

    # Payment endpoints (PayPal + Razorpay)
    path("payment/", include("api.payment_urls")),

    # Health check (for uptime monitoring)
    path("ops/health/", health_check_view, name="health-check"),

    # Django admin (obfuscated URL, not /admin/)
    path(settings.ADMIN_URL, admin.site.urls),

    # Admin Panel (superuser dashboard)
    path("admin-panel/", include("adminpanel.urls")),
]

# Custom admin site branding
admin.site.site_header = "Cortex — Server Admin"
admin.site.site_title = "Cortex Admin"
admin.site.index_title = "Dashboard"
