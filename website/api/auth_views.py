"""
Cortex API — OAuth2 Authentication Views

Endpoints for Desktop IDE authentication:
- GET  /api/v1/auth/login/       — Get OAuth2 authorization URL
- GET  /api/v1/auth/authorize/   — Show "Authorize Device" page
- POST /api/v1/auth/authorize/   — Confirm authorization, redirect to IDE
- POST /api/v1/auth/callback/    — Exchange auth code for tokens
- POST /api/v1/auth/refresh/     — Refresh access token
- POST /api/v1/auth/logout/      — Revoke tokens
- GET  /api/v1/auth/me/          — Get current user info
"""
import hashlib
import json
import logging
import secrets
import time

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .decorators import token_required
from .models import AuthToken
from cortex.account.models import ActiveSession

logger = logging.getLogger("api")
User = get_user_model()


def _get_client_ip(request):
    """Extract client IP from request, handling proxy headers."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


def _get_or_create_auth_code(user, state=""):
    """Create an auth code in the database (survives Django reload)."""
    code = secrets.token_hex(32)
    AuthToken.objects.create(
        user=user,
        access_token=f"pending_{code}",  # placeholder until callback
        refresh_token=f"pending_{secrets.token_hex(32)}",
        expires_at=timezone.now() + timezone.timedelta(minutes=5),
        device_info={"auth_code": code, "state": state, "created_at": time.time()},
        is_active=False,  # not a real token yet
    )
    return code


def _consume_auth_code(code):
    """Look up and consume an auth code. Returns user or None."""
    try:
        # Find the pending token with this auth code
        pending = AuthToken.objects.filter(
            access_token=f"pending_{code}",
            is_active=False,
        ).first()
        if pending:
            device_info = pending.device_info or {}
            if device_info.get("created_at", 0) < time.time() - 300:
                # Expired
                pending.delete()
                return None
            user = pending.user
            pending.delete()  # consume the code
            return user
    except Exception as e:
        logger.error(f"[AUTH] _consume_auth_code error: {e}")
    return None


# =============================================================================
# GET /api/v1/auth/login/
# =============================================================================

@require_GET
def auth_login_view(request):
    """
    Return the OAuth2 authorization URL for desktop login.
    The desktop opens this URL in the user's browser.
    
    Query params:
        - redirect_uri: The callback URL (default: http://127.0.0.1:18923/callback)
        - state: Random state string for CSRF protection
        - os: Device OS (e.g. "Windows")
        - version: IDE version (e.g. "0.0.1")
    """
    redirect_uri = request.GET.get("redirect_uri", "http://127.0.0.1:18923/callback")
    state = request.GET.get("state", secrets.token_hex(16))
    device_os = request.GET.get("os", "Desktop")
    ide_version = request.GET.get("version", "0.0.1")
    
    # Only allow redirects to localhost (security)
    from urllib.parse import urlparse, quote
    parsed = urlparse(redirect_uri)
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        redirect_uri = "http://127.0.0.1:18923/callback"
    
    # Build the authorization URL (passes through to authorize page)
    server_url = getattr(settings, "CORTEX_SERVER_URL", "https://cortex-ide.app")
    
    authorize_url = (
        f"/api/v1/auth/authorize/"
        f"?redirect_uri={quote(redirect_uri, safe='')}"
        f"&state={state}"
        f"&os={quote(device_os, safe='')}"
        f"&version={quote(ide_version, safe='')}"
    )
    auth_url = f"{server_url}/account/login/?next={quote(authorize_url, safe='')}"
    
    return JsonResponse({
        "auth_url": auth_url,
        "state": state,
    })


# =============================================================================
# GET/POST /api/v1/auth/authorize/
# =============================================================================

@csrf_exempt
def auth_authorize_view(request):
    """
    OAuth2 authorization endpoint — industry standard device auth flow.

    GET:  Show "Authorize Device?" confirmation page (like VS Code / GitHub)
    POST: User confirmed → generate auth code → redirect to IDE callback
    """
    redirect_uri = request.GET.get("redirect_uri") or request.POST.get("redirect_uri", "http://127.0.0.1:18923/callback")
    state = request.GET.get("state") or request.POST.get("state", "")

    # Only allow redirects to localhost (security)
    from urllib.parse import urlparse
    parsed = urlparse(redirect_uri)
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        redirect_uri = "http://127.0.0.1:18923/callback"

    # Must be logged in
    if not request.user.is_authenticated:
        # Redirect to login, then back here
        from django.urls import reverse
        login_url = reverse("account:login")
        return_url = f"/api/v1/auth/authorize/?redirect_uri={redirect_uri}&state={state}"
        return HttpResponseRedirect(f"{login_url}?next={return_url}")

    user = request.user

    # GET → Show authorize confirmation page
    if request.method == "GET":
        # Get device info from query params (sent by desktop)
        device_os = request.GET.get("os", "Desktop")
        ide_version = request.GET.get("version", "0.0.1")

        # User initials
        name = user.get_full_name() or user.email
        parts = name.split()
        initials = parts[0][0].upper() + (parts[1][0].upper() if len(parts) > 1 else "")

        context = {
            "user": user,
            "user_initials": initials,
            "redirect_uri": redirect_uri,
            "state": state,
            "device_os": device_os,
            "ide_version": ide_version,
        }
        return render(request, "cortex/account/authorize_device.html", context)

    # POST → User clicked "Authorize" → generate code and redirect
    if request.method == "POST":
        code = _get_or_create_auth_code(user, state)
        callback_url = f"{redirect_uri}?code={code}&state={state}"
        logger.info(f"[AUTH] User {user.email} authorized desktop → redirecting")
        return HttpResponseRedirect(callback_url)

    return JsonResponse({"error": "method_not_allowed"}, status=405)


# =============================================================================
# POST /api/v1/auth/callback/
# =============================================================================

@csrf_exempt
@require_POST
def auth_callback_view(request):
    """
    Exchange an auth code for access + refresh tokens.
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {"error": "invalid_body", "detail": "JSON body required."},
            status=400,
        )
    
    code = body.get("code", "")
    if not code:
        return JsonResponse(
            {"error": "missing_code", "detail": "Auth code is required."},
            status=400,
        )
    
    try:
        # Look up and consume the auth code from DB
        user = _consume_auth_code(code)
        if user is None:
            return JsonResponse(
                {"error": "invalid_code", "detail": "Invalid or expired auth code."},
                status=400,
            )
        
        # Revoke any existing tokens for this user (single-session)
        AuthToken.objects.filter(user=user, is_active=True).update(is_active=False)
        
        # Create new token pair
        device_info = body.get("device_info", {})
        auth_token = AuthToken.create_for_user(user, device_info)
        
        # Track IDE session with actual client IP
        _update_ide_session(user, request, device_info)
        
        logger.info(f"[AUTH] Desktop login: {user.email} from {device_info.get('os', 'unknown')}")
        
        return JsonResponse({
            "access_token": auth_token.access_token,
            "refresh_token": auth_token.refresh_token,
            "expires_at": auth_token.expires_at.isoformat(),
            "user": {
                "id": user.pk,
                "email": user.email,
                "display_name": getattr(user, "display_name", "") or user.get_full_name() or user.email,
                "avatar_url": str(user.avatar.url) if getattr(user, "avatar", None) and user.avatar else None,
                "has_subscription": hasattr(user, "subscription") and user.subscription is not None,
                "plan": user.subscription.plan if hasattr(user, "subscription") and user.subscription else None,
                "plan_display": user.subscription.get_plan_display() if hasattr(user, "subscription") and user.subscription else None,
                "subscription_status": user.subscription.status if hasattr(user, "subscription") and user.subscription else None,
            },
        })
    except Exception as e:
        logger.error(f"[AUTH] callback error: {e}", exc_info=True)
        return JsonResponse(
            {"error": "server_error", "detail": str(e)},
            status=500,
        )


# =============================================================================
# POST /api/v1/auth/login/credentials/
# =============================================================================

@csrf_exempt
@require_POST
def auth_login_credentials_view(request):
    """
    Direct login with email + password (for desktop that doesn't want browser flow).
    
    Body (JSON):
        - email: User email
        - password: User password
        - device_info: Optional device info dict
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {"error": "invalid_body", "detail": "JSON body required."},
            status=400,
        )
    
    email = body.get("email", "")
    password = body.get("password", "")
    
    if not email or not password:
        return JsonResponse(
            {"error": "missing_fields", "detail": "Email and password are required."},
            status=400,
        )
    
    # Authenticate
    user = authenticate(request, username=email, password=password)
    if user is None:
        return JsonResponse(
            {"error": "invalid_credentials", "detail": "Invalid email or password."},
            status=401,
        )
    
    if not user.is_active:
        return JsonResponse(
            {"error": "inactive_user", "detail": "User account is disabled."},
            status=403,
        )
    
    # Revoke existing tokens
    AuthToken.objects.filter(user=user, is_active=True).update(is_active=False)
    
    # Create new token pair
    device_info = body.get("device_info", {})
    auth_token = AuthToken.create_for_user(user, device_info)
    
    # Track IDE session with actual client IP
    _update_ide_session(user, request, device_info)
    
    logger.info(f"[AUTH] Desktop login (credentials): {user.email}")
    
    return JsonResponse({
        "access_token": auth_token.access_token,
        "refresh_token": auth_token.refresh_token,
        "expires_at": auth_token.expires_at.isoformat(),
        "user": {
            "id": user.pk,
            "email": user.email,
            "display_name": getattr(user, "display_name", "") or user.get_full_name() or user.email,
            "avatar_url": str(user.avatar.url) if getattr(user, "avatar", None) and user.avatar else None,
            "has_subscription": hasattr(user, "subscription") and user.subscription is not None,
            "plan": user.subscription.plan if hasattr(user, "subscription") and user.subscription else None,
            "plan_display": user.subscription.get_plan_display() if hasattr(user, "subscription") and user.subscription else None,
            "subscription_status": user.subscription.status if hasattr(user, "subscription") and user.subscription else None,
        },
    })


# =============================================================================
# POST /api/v1/auth/refresh/
# =============================================================================

@csrf_exempt
@require_POST
def auth_refresh_view(request):
    """
    Refresh an access token using a refresh token.
    
    Body (JSON):
        - refresh_token: The refresh token
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {"error": "invalid_body", "detail": "JSON body required."},
            status=400,
        )
    
    refresh_token = body.get("refresh_token", "")
    if not refresh_token:
        return JsonResponse(
            {"error": "missing_token", "detail": "Refresh token is required."},
            status=400,
        )
    
    try:
        auth_token = AuthToken.objects.get(refresh_token=refresh_token, is_active=True)
    except AuthToken.DoesNotExist:
        return JsonResponse(
            {"error": "invalid_token", "detail": "Invalid or revoked refresh token."},
            status=401,
        )
    
    new_access_token = auth_token.refresh()
    
    return JsonResponse({
        "access_token": new_access_token,
        "expires_at": auth_token.expires_at.isoformat(),
    })


# =============================================================================
# POST /api/v1/auth/logout/
# =============================================================================

@csrf_exempt
@require_POST
@token_required
def auth_logout_view(request):
    """Revoke the current user's tokens."""
    AuthToken.objects.filter(user=request.user, is_active=True).update(is_active=False)
    logger.info(f"[AUTH] Desktop logout: {request.user.email}")
    return JsonResponse({"status": "ok"})


# =============================================================================
# GET /api/v1/auth/me/
# =============================================================================

@require_GET
@token_required
def auth_me_view(request):
    """Return the current authenticated user's info."""
    user = request.user
    has_sub = hasattr(user, "subscription") and user.subscription is not None
    return JsonResponse({
        "id": user.pk,
        "email": user.email,
        "display_name": getattr(user, "display_name", "") or user.get_full_name() or user.email,
        "avatar_url": str(user.avatar.url) if getattr(user, "avatar", None) and user.avatar else None,
        "has_subscription": has_sub,
        "plan": user.subscription.plan if has_sub else None,
        "plan_display": user.subscription.get_plan_display() if has_sub else None,
        "subscription_status": user.subscription.status if has_sub else None,
        "date_joined": user.date_joined.isoformat(),
        "last_login": user.last_login.isoformat() if user.last_login else None,
    })


def _update_ide_session(user, request, device_info=None):
    """Create or update an IDE session record with the actual client IP."""
    client_ip = _get_client_ip(request)
    os_info = (device_info or {}).get("os", "")
    user_agent = request.META.get("HTTP_USER_AGENT", f"Cortex IDE {os_info}".strip())

    # Mark all existing IDE sessions as not current
    ActiveSession.objects.filter(user=user, client_type=ActiveSession.ClientType.IDE).update(is_current=False)

    ActiveSession.objects.create(
        user=user,
        client_type=ActiveSession.ClientType.IDE,
        ip_address=client_ip,
        location="",
        user_agent=user_agent,
        is_current=True,
    )
