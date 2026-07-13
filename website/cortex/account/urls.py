"""
Cortex Account — URL Routing

Authentication:
/account/login/                     -> Login page (email + password)
/account/signup/                    -> Signup page (create account)
/account/logout/                    -> Logout action (POST only)
/account/password-reset/            -> Request password reset email
/account/password-reset-confirm/    -> Set new password (from email link)
/account/password-reset-complete/   -> Password reset success

Account Panel:
/account/                -> Redirect to /account/profile/
/account/profile/        -> Profile page (name, email, avatar, sessions)
/account/usage/          -> Credit usage stats and log
/account/plan/           -> Current plan, upgrade, billing, orders
/account/integrations/   -> API keys, BYOK providers
/account/security/       -> Password, 2FA, sessions, API keys, delete

/account/api/profile/update/ -> AJAX profile update
"""
from django.urls import path

from . import views

app_name = "account"

urlpatterns = [
    # Default redirect
    path("", views.account_redirect, name="index"),

    # ---- Authentication ----
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup_view, name="signup"),
    path("verify/", views.verify_otp_view, name="verify-otp"),
    path("logout/", views.logout_view, name="logout"),
    path("password-reset/", views.password_reset_view, name="password-reset"),
    path(
        "password-reset-confirm/<str:uidb64>/<str:token>/",
        views.password_reset_confirm_view,
        name="password-reset-confirm",
    ),
    path(
        "password-reset-verify/",
        views.password_reset_verify_view,
        name="password-reset-verify",
    ),
    path(
        "password-reset-complete/",
        views.password_reset_complete_view,
        name="password-reset-complete",
    ),

    # ---- Account Panel Pages ----
    path("profile/", views.profile_view, name="profile"),
    path("usage/", views.usage_view, name="usage"),
    path("plan/", views.plan_view, name="plan"),
    path("plan/invoice/<int:order_id>/", views.invoice_view, name="invoice"),
    path("integrations/", views.integrations_view, name="integrations"),
    path("pricing/", views.pricing_view, name="pricing"),
    path("security/", views.security_view, name="security"),
    path("security/password/", views.change_password_view, name="change-password"),
    path("security/delete/", views.delete_account_view, name="delete-account"),

    # ---- AJAX API ----
    path("api/profile/update/", views.api_profile_update, name="api-profile-update"),
]
