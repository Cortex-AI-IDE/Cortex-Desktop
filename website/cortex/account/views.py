"""
Cortex Account — Views

Auth views (login, signup, logout, password reset) + account panel views.
Each view provides context data (user info, subscription, credits, usage logs)
that the templates render.
"""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.views import (
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetCompleteView,
)
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import login_required
from .forms import LoginForm, SignupForm, ProfileForm
from .models import ActiveSession, ApiKey, Order, User


# =============================================================================
# Auth: Login
# =============================================================================

def login_view(request):
    """
    GET  /account/login/  — show login form
    POST /account/login/  — authenticate + redirect
    """
    next_url = request.GET.get("next", "")

    # Already logged in? Respect ?next= param (needed for OAuth flow)
    if request.user.is_authenticated:
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect("account:profile")

    error_message = None

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data["user"]

            if not user.email_verified and settings.EMAIL_VERIFICATION_ENABLED:
                error_message = "Please verify your email before signing in. Check your inbox."
            else:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")

                # Mark prior web sessions as not current, then create a new one
                ActiveSession.objects.filter(user=user, client_type=ActiveSession.ClientType.WEB).update(is_current=False)
                ActiveSession.objects.create(
                    user=user,
                    client_type=ActiveSession.ClientType.WEB,
                    ip_address=_get_client_ip(request),
                    location="",
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    is_current=True,
                )

                # Redirect to ?next= or profile
                if next_url and next_url.startswith("/"):
                    return redirect(next_url)
                return redirect("account:profile")
        else:
            error_message = "Invalid email or password."
    else:
        form = LoginForm()

    context = {
        "form": form,
        "error_message": error_message,
        "next_url": next_url,
    }
    return render(request, "cortex/account/login.html", context)


# =============================================================================
# Auth: Signup
# =============================================================================

def signup_view(request):
    """
    GET  /account/signup/  — show signup form
    POST /account/signup/  — create user + log in
    No free plan — user must purchase Pro to get credits.
    """
    if request.user.is_authenticated:
        return redirect("account:profile")

    error_message = None

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()

            # ── No-SMTP mode: sign the user straight in WITHOUT faking
            # verification. email_verified stays False (the truth) and the
            # profile page shows a "Not verified" badge until they verify. ──
            if not settings.EMAIL_VERIFICATION_ENABLED:
                user.email_verified = False
                user.save()
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                ActiveSession.objects.create(
                    user=user,
                    client_type=ActiveSession.ClientType.WEB,
                    ip_address=_get_client_ip(request),
                    location="",
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    is_current=True,
                )
                return redirect("account:profile")

            user.email_verified = False
            user.save()

            # Generate OTP and store in session
            import random
            otp = str(random.randint(100000, 999999))
            request.session["verify_otp"] = otp
            request.session["verify_user_id"] = user.pk
            request.session.set_expiry(600)  # 10 minutes

            # Send branded OTP email (HTML + plain-text fallback)
            from .emails import send_otp_email
            send_otp_email(user, otp, purpose="signup")

            return redirect("account:verify-otp")
        else:
            error_message = "Please fix the errors below."
    else:
        form = SignupForm()

    context = {
        "form": form,
        "error_message": error_message,
    }
    return render(request, "cortex/account/signup.html", context)


# =============================================================================
# Auth: Logout
# =============================================================================

@require_POST
def logout_view(request):
    """
    POST /account/logout/

    Marks all web sessions as inactive, logs out, redirects to home.
    """
    if request.user.is_authenticated:
        # Mark current web session as not current
        ActiveSession.objects.filter(
            user=request.user,
            client_type=ActiveSession.ClientType.WEB,
            is_current=True,
        ).update(is_current=False)

    logout(request)
    return redirect("cortex:index")


# =============================================================================
# Auth: Password Reset (uses Django's built-in views, customized templates)
# =============================================================================

def password_reset_view(request):
    """
    GET  /account/password-reset/  — show form to enter email
    POST /account/password-reset/  — email a 6-digit OTP (if account exists)
                                     and go to the code + new password page

    The OTP is sent from settings.NOTIFICATION_EMAIL. We ALWAYS redirect to
    the verify page whether or not the account exists — an attacker can't
    tell the difference (no OTP is stored for unknown emails, so every code
    they enter simply fails).
    """
    from django.contrib.auth.forms import PasswordResetForm
    from django.core.mail import send_mail

    error_message = None

    if request.method == "POST":
        form = PasswordResetForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            user = User.objects.filter(email__iexact=email, is_active=True).first()

            # Reset any previous attempt
            for key in ("reset_otp", "reset_user_id", "reset_otp_expires"):
                request.session.pop(key, None)
            request.session["reset_email"] = email

            if user:
                import random
                import time
                otp = str(random.randint(100000, 999999))
                request.session["reset_otp"] = otp
                request.session["reset_user_id"] = user.pk
                request.session["reset_otp_expires"] = time.time() + 600  # 10 min

                from .emails import send_otp_email
                send_otp_email(user, otp, purpose="password_reset")

            return redirect("account:password-reset-verify")
        else:
            error_message = "Please enter a valid email address."
    else:
        form = PasswordResetForm()

    context = {
        "form": form,
        "error_message": error_message,
    }
    return render(request, "cortex/account/password_reset.html", context)


def password_reset_verify_view(request):
    """
    GET/POST /account/password-reset-verify/ — enter the emailed 6-digit
    code plus a new password. On success the password is changed and the
    user continues to the sign-in page.
    """
    import time
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    email = request.session.get("reset_email")
    if not email:
        return redirect("account:password-reset")

    error = None

    if request.method == "POST":
        entered = request.POST.get("otp", "").strip()
        stored = request.session.get("reset_otp", "")
        expires = request.session.get("reset_otp_expires", 0)
        user_id = request.session.get("reset_user_id")
        password1 = request.POST.get("new_password1", "")
        password2 = request.POST.get("new_password2", "")

        if not stored or not user_id or time.time() > expires:
            error = "This code has expired. Please request a new one."
        elif entered != stored:
            error = "Invalid code. Please try again."
        elif password1 != password2:
            error = "Passwords don't match."
        else:
            try:
                user = User.objects.get(pk=user_id, is_active=True)
            except User.DoesNotExist:
                return redirect("account:password-reset")
            try:
                validate_password(password1, user=user)
            except ValidationError as ve:
                error = " ".join(ve.messages)
            else:
                user.set_password(password1)
                user.save()
                for key in ("reset_otp", "reset_user_id",
                            "reset_otp_expires", "reset_email"):
                    request.session.pop(key, None)
                return redirect("account:password-reset-complete")

    return render(request, "cortex/account/password_reset_verify.html", {
        "email": email,
        "error": error,
    })


def password_reset_confirm_view(request, uidb64, token):
    """
    GET  /account/password-reset-confirm/<uidb64>/<token>/  — show new password form
    POST /account/password-reset-confirm/<uidb64>/<token>/  — set new password
    """
    from django.contrib.auth.forms import SetPasswordForm
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.http import urlsafe_base64_decode
    from django.contrib.auth import get_user_model

    User = get_user_model()
    validlink = False
    user = None

    try:
        uid = urlsafe_base64_decode(uidb64).decode()
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        pass

    if user is not None and default_token_generator.check_token(user, token):
        validlink = True

    if request.method == "POST" and validlink:
        form = SetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            return redirect("account:password-reset-complete")
    else:
        form = SetPasswordForm(user) if validlink else None

    context = {
        "form": form,
        "validlink": validlink,
    }
    return render(request, "cortex/account/password_reset_confirm.html", context)


def password_reset_complete_view(request):
    """GET /account/password-reset-complete/"""
    return render(request, "cortex/account/password_reset_complete.html")


# =============================================================================
# Redirect /account/ -> /account/profile/
# =============================================================================

@login_required
def account_redirect(request):
    """Default account page redirects to profile."""
    return redirect("account:profile")


# =============================================================================
# Profile Page
# =============================================================================

@login_required
def profile_view(request):
    """
    GET  /account/profile/
    POST /account/profile/ (update name/display_name/timezone)
    """
    user = request.user

    if request.method == "POST":
        form = ProfileForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            return redirect("account:profile")
    else:
        form = ProfileForm(instance=user)

    # Get active sessions — deduplicated by client_type + ip_address,
    # keeping the most recent last_active time per unique device.
    from django.db.models import Max
    raw_sessions = (
        ActiveSession.objects.filter(user=user)
        .values("client_type", "ip_address")
        .annotate(latest_active=Max("last_active"))
        .order_by("-latest_active")
    )
    client_labels = dict(ActiveSession.ClientType.choices)
    sessions = [
        {
            "client_type": client_labels.get(s["client_type"], s["client_type"]),
            "ip_address": s["ip_address"],
            "latest_active": s["latest_active"],
        }
        for s in raw_sessions
    ]

    context = {
        "active_tab": "profile",
        "page_title": "Profile",
        "form": form,
        "sessions": sessions,
    }
    return render(request, "cortex/account/account_profile.html", context)


# =============================================================================
# Usage Page
# =============================================================================

@login_required
def usage_view(request):
    """
    GET /account/usage/

    Shows subscription status and service usage (OCR, Embeddings, Web Search).
    All LLM usage is BYOK — tracked locally by desktop, not on server.
    """
    user = request.user
    subscription = getattr(user, "subscription", None)
    
    # Normalize plan name (handle legacy "starter" plans)
    if subscription:
        if subscription.plan in ("starter", "pro"):
            subscription.display_plan = "Pro"
        elif subscription.plan == "pro_yearly":
            subscription.display_plan = "Pro (Yearly)"
        else:
            subscription.display_plan = subscription.plan.title()
    
    # Calculate usage stats
    from django.db.models import Sum, Count
    from django.utils import timezone
    from api.models import UsageLog
    
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    monthly_stats = {}
    if subscription:
        monthly_stats = UsageLog.objects.filter(
            subscription=subscription,
            created_at__gte=month_start
        ).aggregate(
            total_ocr_pages=Sum('ocr_pages'),
            total_requests=Count('id'),
            total_embedding_tokens=Sum('input_tokens', filter=models.Q(model_id="siliconflow-embedding")),
            total_web_searches=Count('id', filter=models.Q(model_id="web-search")),
        )

    context = {
        "active_tab": "usage",
        "page_title": "Usage",
        "subscription": subscription,
        "monthly_ocr_pages": monthly_stats.get("total_ocr_pages") or 0,
        "monthly_requests": monthly_stats.get("total_requests") or 0,
        "monthly_embedding_tokens": monthly_stats.get("total_embedding_tokens") or 0,
        "monthly_web_searches": monthly_stats.get("total_web_searches") or 0,
    }
    return render(request, "cortex/account/account_usage.html", context)


# =============================================================================
# Plan & Billing Page
# =============================================================================

@login_required
def plan_view(request):
    """
    GET /account/plan/

    Shows current plan, upgrade options, payment method, and order history.
    """
    user = request.user
    subscription = getattr(user, "subscription", None)
    orders = Order.objects.filter(user=user)[:20]  # Last 20 orders

    context = {
        "active_tab": "plan",
        "page_title": "Plan & Billing",
        "subscription": subscription,
        "current_plan": subscription.plan if subscription else "none",
        "orders": orders,
    }
    return render(request, "cortex/account/account_plan.html", context)


# =============================================================================
# Invoice Download
# =============================================================================

@login_required
def invoice_view(request, order_id):
    """
    GET /account/plan/invoice/<order_id>/
    Returns a downloadable PDF invoice for a completed order.
    """
    from django.http import HttpResponse
    from django.shortcuts import get_object_or_404
    from xhtml2pdf import pisa
    from io import BytesIO
    from django.template.loader import render_to_string
    from django.utils import timezone

    order = get_object_or_404(Order, id=order_id, user=request.user)
    subscription = getattr(request.user, "subscription", None)
    payment = order.payment

    html = render_to_string("cortex/account/invoice.html", {
        "order": order,
        "user": request.user,
        "subscription": subscription,
        "payment": payment,
        "now": timezone.now(),
    })

    # Generate PDF
    result = BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=result, encoding="utf-8")
    if pisa_status.err:
        return HttpResponse("Error generating PDF", status=500)

    result.seek(0)
    filename = f"Cortex_Invoice_{order.id:05d}.pdf"
    response = HttpResponse(result.read(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# =============================================================================
# Integrations Page
# =============================================================================

@login_required
def integrations_view(request):
    """
    GET /account/integrations/

    Shows BYOK API key status and connected providers.
    """
    user = request.user
    api_keys = ApiKey.objects.filter(user=user, is_active=True)

    context = {
        "active_tab": "integrations",
        "page_title": "Integrations",
        "api_keys": api_keys,
        "byok_providers": [
            {"name": "OpenAI", "configured": False},
            {"name": "Qwen", "configured": False},
            {"name": "Kimi", "configured": False},
            {"name": "OpenRouter", "configured": False},
            {"name": "Anthropic", "configured": False},
        ],
    }
    return render(request, "cortex/account/account_integrations.html", context)


# =============================================================================
# Security Page
# =============================================================================

@login_required
def pricing_view(request):
    """
    GET /account/pricing/

    Internal pricing page for logged-in users.
    Shows plan comparison with upgrade/downgrade actions.
    Keeps users inside the account panel (no redirect to public landing page).
    """
    user = request.user
    subscription = getattr(user, "subscription", None)
    current_plan = subscription.plan if subscription else "none"

    context = {
        "active_tab": "plan",
        "page_title": "Pricing",
        "current_plan": current_plan,
        "paypal_client_id": getattr(settings, "PAYPAL_CLIENT_ID", ""),
        "razorpay_key_id": getattr(settings, "RAZORPAY_KEY_ID", ""),
        "paypal_mode": getattr(settings, "PAYPAL_MODE", "sandbox"),
        "razorpay_mode": getattr(settings, "RAZORPAY_MODE", "test"),
        "razorpay_enabled": getattr(settings, "RAZORPAY_ENABLED", True),
    }
    return render(request, "cortex/account/account_pricing.html", context)


@login_required
def security_view(request):
    """
    GET /account/security/

    Shows password management, 2FA status, active sessions, API keys, and delete account.
    """
    user = request.user
    from django.db.models import Max
    raw_sessions = (
        ActiveSession.objects.filter(user=user)
        .values("client_type", "ip_address")
        .annotate(latest_active=Max("last_active"))
        .order_by("-latest_active")
    )
    client_labels = dict(ActiveSession.ClientType.choices)
    sessions = [
        {
            "client_type": client_labels.get(s["client_type"], s["client_type"]),
            "ip_address": s["ip_address"],
            "latest_active": s["latest_active"],
        }
        for s in raw_sessions
    ]

    context = {
        "active_tab": "security",
        "page_title": "Security",
        "sessions": sessions,
        "has_usable_password": user.has_usable_password(),
    }
    return render(request, "cortex/account/account_security.html", context)


# =============================================================================
# Change Password
# =============================================================================

@login_required
def change_password_view(request):
    """
    POST /account/security/password/
    
    Change the user's password. Requires current password + new password.
    """
    from django.contrib.auth import update_session_auth_hash
    
    if request.method != "POST":
        return redirect("account:security")
    
    user = request.user
    current_password = request.POST.get("current_password", "")
    new_password = request.POST.get("new_password", "")
    confirm_password = request.POST.get("confirm_password", "")
    
    error = None
    success = None
    
    if not user.check_password(current_password):
        error = "Current password is incorrect."
    elif len(new_password) < 8:
        error = "New password must be at least 8 characters."
    elif new_password != confirm_password:
        error = "New passwords do not match."
    else:
        user.set_password(new_password)
        user.save()
        update_session_auth_hash(request, user)
        success = "Password changed successfully."
    
    # Re-render security page with message
    from django.db.models import Max
    raw_sessions = (
        ActiveSession.objects.filter(user=user)
        .values("client_type", "ip_address")
        .annotate(latest_active=Max("last_active"))
        .order_by("-latest_active")
    )
    client_labels = dict(ActiveSession.ClientType.choices)
    sessions = [
        {
            "client_type": client_labels.get(s["client_type"], s["client_type"]),
            "ip_address": s["ip_address"],
            "latest_active": s["latest_active"],
        }
        for s in raw_sessions
    ]
    api_keys = ApiKey.objects.filter(user=user, is_active=True)
    
    context = {
        "active_tab": "security",
        "page_title": "Security",
        "sessions": sessions,
        "has_usable_password": user.has_usable_password(),
        "password_error": error,
        "password_success": success,
    }
    return render(request, "cortex/account/account_security.html", context)


# =============================================================================
# Delete Account
# =============================================================================

@login_required
def delete_account_view(request):
    """
    POST /account/security/delete/
    
    Permanently deletes the user account and all associated data.
    """
    from django.contrib.auth import logout as auth_logout
    from django.contrib import messages
    
    if request.method != "POST":
        return redirect("account:security")
    
    user = request.user
    auth_logout(request)
    user.delete()
    
    return redirect("/")


# =============================================================================
# API: Profile Update (AJAX)
# =============================================================================

@login_required
def api_profile_update(request):
    """
    POST /account/api/profile/update/

    AJAX endpoint for updating profile fields.
    Returns JSON response.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    user = request.user
    form = ProfileForm(request.POST, instance=user)

    if form.is_valid():
        form.save()
        return JsonResponse({
            "success": True,
            "display_name": user.get_display_name_or_email(),
        })

    return JsonResponse({"errors": form.errors}, status=400)


# =============================================================================
# Helpers
# =============================================================================

def _get_client_ip(request):
    """Extract client IP from request, handling proxy headers."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


# =============================================================================
# OTP Verification
# =============================================================================

def verify_otp_view(request):
    """GET/POST /account/verify/ — Enter OTP to verify email."""
    user_id = request.session.get("verify_user_id")

    if not user_id:
        return redirect("account:signup")

    try:
        user = User.objects.get(pk=user_id, email_verified=False)
    except User.DoesNotExist:
        return redirect("account:signup")

    error = None

    if request.method == "POST":
        entered = request.POST.get("otp", "").strip()
        stored = request.session.get("verify_otp", "")

        if entered == stored:
            user.email_verified = True
            user.save()

            del request.session["verify_otp"]
            del request.session["verify_user_id"]

            login(request, user, backend="django.contrib.auth.backends.ModelBackend")

            ActiveSession.objects.filter(user=user, client_type=ActiveSession.ClientType.WEB).update(is_current=False)
            ActiveSession.objects.create(
                user=user,
                client_type=ActiveSession.ClientType.WEB,
                ip_address=_get_client_ip(request),
                location="",
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                is_current=True,
            )
            return redirect("account:profile")
        else:
            error = "Invalid code. Please try again."

    return render(request, "cortex/account/verify_otp.html", {
        "email": user.email,
        "error": error,
    })

