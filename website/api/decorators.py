"""
Cortex API — Authentication Decorators

Provides token-based authentication for Desktop IDE API endpoints.
The desktop sends `Authorization: Bearer <access_token>` header.
"""
import functools
import logging

from django.http import JsonResponse

from .models import AuthToken

logger = logging.getLogger("api")


def token_required(view_func):
    """
    Decorator that requires a valid access token.
    Attaches the authenticated user to request.user.
    
    Usage:
        @token_required
        def my_view(request):
            user = request.user  # guaranteed to be authenticated
            ...
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return JsonResponse(
                {"error": "missing_token", "detail": "Authorization: Bearer <token> header required."},
                status=401,
            )
        
        token = auth_header[7:].strip()
        if not token:
            return JsonResponse(
                {"error": "empty_token", "detail": "Access token is empty."},
                status=401,
            )
        
        user = AuthToken.validate_access_token(token)
        if user is None:
            return JsonResponse(
                {"error": "invalid_token", "detail": "Invalid or expired access token."},
                status=401,
            )
        
        if not user.is_active:
            return JsonResponse(
                {"error": "inactive_user", "detail": "User account is disabled."},
                status=403,
            )
        
        request.user = user
        _record_client_version(request, user)
        return view_func(request, *args, **kwargs)

    return wrapper


def _record_client_version(request, user):
    """Track which IDE version each user runs (X-Cortex-Version header).

    Surfaced in the admin panel (Users list + version distribution).
    Throttled: writes only when the version CHANGED or the last ping is
    older than an hour — not one UPDATE per API call. Never raises.
    """
    try:
        version = request.META.get("HTTP_X_CORTEX_VERSION", "").strip()[:20]
        if not version:
            return
        from django.utils import timezone
        now = timezone.now()
        stale = (
            user.last_seen_at is None
            or (now - user.last_seen_at).total_seconds() > 3600
        )
        if version != user.last_seen_version or stale:
            type(user).objects.filter(pk=user.pk).update(
                last_seen_version=version, last_seen_at=now,
            )
    except Exception:
        pass  # version telemetry must never break an API request


def optional_token(view_func):
    """
    Decorator that extracts user from token if present, but doesn't require it.
    request.user will be None if no valid token is provided.
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            user = AuthToken.validate_access_token(token)
            request.user = user
        else:
            request.user = None
        return view_func(request, *args, **kwargs)
    
    return wrapper
