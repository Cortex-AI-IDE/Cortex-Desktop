"""
Cortex API — Views

Endpoints:
- GET  /api/v1/models/config/           — Model config for IDE (public, cached)
- POST /api/v1/subscription/validate/   — Validate subscription license key
- GET  /api/v1/subscription/credits/    — Get credit balance for a subscription
- POST /api/v1/subscription/credits/    — Consume credits (used by proxy)
- POST /api/v1/proxy/chat/              — Proxy included-model requests (DeepSeek/MiMo/Mistral/SiliconFlow)
- POST /api/v1/telemetry/crash/         — Crash report ingestion (opt-in)
- GET  /ops/health/                     — Health check for uptime monitoring
"""
import hashlib
import json
import logging
import os
from decimal import Decimal

from django.conf import settings
from django.db import models as db_models
from django.http import FileResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import CrashReport, Subscription, UsageLog

logger = logging.getLogger("api")


# =============================================================================
# Helper: extract license from request
# =============================================================================

def _get_license(request):
    """Extract license key from Authorization header or query param."""
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.GET.get("license_key", "").strip()


# =============================================================================
# POST /api/v1/subscription/validate/
# =============================================================================

@require_POST
@csrf_exempt
def subscription_validate_view(request):
    """
    Validate a subscription license key.
    Called by the IDE on startup and periodically.

    Request:  {"license_key": "cortex_abc123..."}
    Response: {"valid": true, "plan": "pro", "credits_remaining": 1234, ...}
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    license_key = body.get("license_key", "").strip()
    sub, error = Subscription.validate_license(license_key)

    if sub is None:
        return JsonResponse({
            "valid": False,
            "error": error,
        }, status=401)

    return JsonResponse({
        "valid": True,
        "plan": sub.plan,
        "plan_display": sub.get_plan_display(),
        "status": sub.status,
        "email": sub.email,
        "period_end": sub.current_period_end.isoformat(),
    })


# =============================================================================
# GET /api/v1/subscription/credits/
# =============================================================================

@require_GET
@csrf_exempt
def subscription_credits_view(request):
    """
    Get current subscription info.
    Called by the IDE to display subscription status in the UI.
    """
    license_key = _get_license(request)
    sub, error = Subscription.validate_license(license_key)

    if sub is None:
        return JsonResponse({"error": error}, status=401)

    return JsonResponse({
        "plan": sub.plan,
        "status": sub.status,
        "period_end": sub.current_period_end.isoformat(),
    })


# =============================================================================
# POST /api/v1/proxy/chat/ — Proxy requests to included models
# =============================================================================

@require_POST
@csrf_exempt
def proxy_chat_view(request):
    """
    Proxy subscription service requests (OCR, embeddings).
    
    All LLM chat (MiMo, DeepSeek, OpenAI, etc.) is BYOK — user pays provider directly.
    This endpoint only handles:
    - Mistral OCR (image text extraction)
    - SiliconFlow embeddings (semantic search)
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    # Validate license
    license_key = body.get("license_key", "").strip()
    sub, error = Subscription.validate_license(license_key)
    if sub is None:
        return JsonResponse({"error": error, "code": "INVALID_LICENSE"}, status=401)

    service = body.get("service", "")
    
    # Service routing
    if service == "mistral_ocr":
        return _proxy_mistral_ocr(body, sub)
    elif service == "siliconflow_embeddings":
        return _proxy_siliconflow_embeddings(body, sub)
    elif service == "web_search":
        return _proxy_web_search(body, sub)
    else:
        return JsonResponse({
            "error": "Invalid service. Use 'mistral_ocr', 'siliconflow_embeddings', or 'web_search'.",
            "code": "INVALID_SERVICE",
        }, status=400)


def _proxy_mistral_ocr(body, subscription):
    """Proxy Mistral OCR request."""
    import requests as http_client
    from decouple import config as env_config

    api_key = env_config("MISTRAL_API_KEY", default="")
    if not api_key:
        return JsonResponse({"error": "Mistral API key not configured.", "code": "MISSING_KEY"}, status=500)

    # Build Mistral OCR request
    image_url = body.get("image_url", "")
    prompt = body.get("prompt", "Extract all text from this image")
    
    payload = {
        "model": "mistral-large-latest",
        "messages": [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": prompt}
            ]}
        ],
        "max_tokens": 4096,
    }

    try:
        resp = http_client.post(
            "https://api.mistral.ai/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=60,
        )
        
        if resp.status_code != 200:
            return JsonResponse({"error": f"Mistral API error: {resp.status_code}", "code": "PROVIDER_ERROR"}, status=502)
        
        data = resp.json()
        
        # Log usage
        UsageLog.objects.create(
            subscription=subscription,
            model_id="mistral-large-latest",
            ocr_pages=1,
            input_tokens=data.get("usage", {}).get("prompt_tokens", 0),
            output_tokens=data.get("usage", {}).get("completion_tokens", 0),
        )
        
        return JsonResponse({"status": "success", "data": data})
        
    except Exception as e:
        return JsonResponse({"error": str(e), "code": "PROVIDER_ERROR"}, status=502)


def _proxy_siliconflow_embeddings(body, subscription):
    """Proxy SiliconFlow embeddings request."""
    import requests as http_client
    from decouple import config as env_config

    api_key = env_config("SILICONFLOW_API_KEY", default="")
    if not api_key:
        return JsonResponse({"error": "SiliconFlow API key not configured.", "code": "MISSING_KEY"}, status=500)

    text = body.get("text", "")
    model = body.get("model", "Qwen/Qwen3-Embedding-4B")

    payload = {
        "model": model,
        "input": text,
        "encoding_format": "float",
    }

    try:
        # timeout MUST stay well under gunicorn's worker timeout (30s default):
        # a 30s outbound call raced the worker kill — Cloudflare then served
        # its HTML 502 page to the IDE (the client-side "502 storm"). At 20s
        # a slow SiliconFlow returns a clean JSON error instead.
        resp = http_client.post(
            "https://api.siliconflow.com/v1/embeddings",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=20,
        )
        
        if resp.status_code != 200:
            return JsonResponse({"error": f"SiliconFlow API error: {resp.status_code}", "code": "PROVIDER_ERROR"}, status=502)
        
        data = resp.json()

        # SiliconFlow returns prompt_tokens (not total_tokens) for embeddings
        usage = data.get("usage", {})
        embedding_tokens = usage.get("total_tokens") or usage.get("prompt_tokens", 0)

        # Log usage
        UsageLog.objects.create(
            subscription=subscription,
            model_id="siliconflow-embedding",
            input_tokens=embedding_tokens,
            output_tokens=0,
        )

        return JsonResponse({"status": "success", "data": data})
        
    except Exception as e:
        return JsonResponse({"error": str(e), "code": "PROVIDER_ERROR"}, status=502)


def _proxy_web_search(body, subscription):
    """Proxy web search request via SerpAPI."""
    import requests as http_client
    from decouple import config as env_config
    from urllib.parse import quote

    api_key = env_config("SERPAPI_API_KEY", default="")
    if not api_key:
        return JsonResponse({"error": "SerpAPI key not configured.", "code": "MISSING_KEY"}, status=500)

    query = body.get("query", "")
    if not query:
        return JsonResponse({"error": "Query is required.", "code": "MISSING_QUERY"}, status=400)

    try:
        encoded = quote(query)
        url = f"https://serpapi.com/search?q={encoded}&api_key={api_key}&engine=google&num=10&gl=us&hl=en"
        resp = http_client.get(url, timeout=15)

        if resp.status_code != 200:
            return JsonResponse({"error": f"SerpAPI error: {resp.status_code}", "code": "PROVIDER_ERROR"}, status=502)

        data = resp.json()

        # Extract organic results
        results = []
        for item in data.get("organic_results", [])[:10]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })

        # Log usage
        UsageLog.objects.create(
            subscription=subscription,
            model_id="web-search",
            input_tokens=0,
            output_tokens=0,
        )

        return JsonResponse({"status": "success", "results": results, "query": query})

    except Exception as e:
        return JsonResponse({"error": str(e), "code": "PROVIDER_ERROR"}, status=502)


# =============================================================================
# POST /api/v1/telemetry/crash/
# =============================================================================

@require_POST
@csrf_exempt
def crash_report_view(request):
    """
    Accept a crash report from the IDE (opt-in telemetry).

    Validates:
    - Request body is valid JSON with required fields
    - Device hash is provided (one-way SHA-256, never raw hardware ID)
    - Rate limit: max N reports per device per hour (configurable)
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    required_fields = ["device_id", "ide_version", "os_version", "error_type", "error_message"]
    missing = [f for f in required_fields if not body.get(f)]
    if missing:
        return JsonResponse(
            {"error": f"Missing required fields: {', '.join(missing)}"},
            status=400,
        )

    device_id = str(body["device_id"]).strip()
    device_hash = hashlib.sha256(device_id.encode()).hexdigest()

    rate_limit = getattr(settings, "TELEMETRY_RATE_LIMIT", 10)
    recent_count = CrashReport.count_recent(device_hash, hours=1)
    if recent_count >= rate_limit:
        logger.warning(
            "Rate limit hit for device %s — %d reports in last hour",
            device_hash[:12], recent_count,
        )
        return JsonResponse(
            {"error": "Rate limit exceeded. Try again later.", "retry_after_seconds": 3600},
            status=429,
        )

    report = CrashReport.objects.create(
        device_hash=device_hash,
        ide_version=str(body["ide_version"])[:20],
        os_version=str(body["os_version"])[:100],
        error_type=str(body["error_type"])[:200],
        error_message=str(body["error_message"])[:5000],
        stack_trace=str(body.get("stack_trace", ""))[:10000],
        context=str(body.get("context", ""))[:200],
    )

    logger.info(
        "Crash report received: [%s] v%s from device %s",
        report.error_type, report.ide_version, device_hash[:12],
    )

    return JsonResponse(
        {"status": "accepted", "report_id": report.id},
        status=202,
    )


# =============================================================================
# GET /ops/health/
# =============================================================================

@require_GET
def health_check_view(request):
    """
    Simple health check endpoint for uptime monitoring.
    Uses cache to avoid hammering DB on frequent health checks.
    """
    from django.core.cache import cache

    cached = cache.get("health_check")
    if cached and not request.GET.get("force"):
        return JsonResponse(cached, status=200)

    checks = {}
    healthy = True

    # Database check
    try:
        from django.db import connection
        connection.ensure_connection()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        healthy = False

    # Subscription count
    active_subs = Subscription.objects.filter(status=Subscription.Status.ACTIVE).count()
    checks["active_subscriptions"] = active_subs

    # Crash report count (last 24h)
    yesterday = timezone.now() - timezone.timedelta(hours=24)
    crash_count_24h = CrashReport.objects.filter(created_at__gte=yesterday).count()
    checks["crash_reports_24h"] = crash_count_24h

    status_code = 200 if healthy else 503
    result = {
        "status": "healthy" if healthy else "unhealthy",
        "version": "2.8.0",
        "timestamp": timezone.now().isoformat(),
        "checks": checks,
    }

    # Cache for 30 seconds to reduce DB load
    if healthy:
        cache.set("health_check", result, 30)

    return JsonResponse(result, status=status_code)


# =============================================================================
# Profile endpoints (Desktop)
# =============================================================================

@csrf_exempt
def profile_view(request):
    """
    GET  /api/v1/profile/  — Get user profile
    PATCH /api/v1/profile/ — Update user profile
    """
    from .decorators import token_required
    
    @token_required
    def _get(request):
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
    
    @token_required
    def _patch(request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "invalid_body"}, status=400)
        
        user = request.user
        if "display_name" in body:
            user.display_name = body["display_name"]
        if "email" in body:
            user.email = body["email"]
        user.save()
        
        return JsonResponse({
            "id": user.pk,
            "email": user.email,
            "display_name": user.display_name,
        })
    
    if request.method == "GET":
        return _get(request)
    elif request.method == "PATCH":
        return _patch(request)
    return JsonResponse({"error": "method_not_allowed"}, status=405)


# =============================================================================
# Usage endpoints (Desktop)
# =============================================================================

@require_GET
def usage_summary_view(request):
    """GET /api/v1/usage/summary/ — Get usage summary for the current user."""
    from .decorators import token_required
    
    @token_required
    def _view(request):
        user = request.user

        # Get subscription
        try:
            sub = Subscription.objects.select_related().get(user=user, status=Subscription.Status.ACTIVE)
        except Subscription.DoesNotExist:
            sub = None

        # Get usage for current month
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly_usage = UsageLog.objects.filter(
            subscription__user=user,
            created_at__gte=month_start,
        ).aggregate(
            total_input=db_models.Sum("input_tokens"),
            total_output=db_models.Sum("output_tokens"),
            total_requests=db_models.Count("id"),
            # Per-service breakdown — the desktop Settings "Service Usage"
            # panel shows these. Aggregated ACCOUNT-wide (all devices), the
            # same query the website /account/usage/ page runs, so both
            # screens always agree. Bug history: desktop showed per-device
            # local JSON counters while the site showed this DB — the two
            # could never match for anyone using Cortex on 2+ machines.
            ocr_pages=db_models.Sum("ocr_pages"),
            embedding_tokens=db_models.Sum(
                "input_tokens", filter=db_models.Q(model_id="siliconflow-embedding")),
            web_searches=db_models.Count(
                "id", filter=db_models.Q(model_id="web-search")),
        )

        return JsonResponse({
            "subscription": {
                "plan": sub.plan if sub else "free",
                "status": sub.status if sub else "none",
                "is_active": sub is not None,
                "license_key": sub.license_key if sub else None,
            },
            "usage": {
                "tokens_this_month": (monthly_usage["total_input"] or 0) + (monthly_usage["total_output"] or 0),
                "requests_this_month": monthly_usage["total_requests"] or 0,
                "services": {
                    "ocr_pages": monthly_usage["ocr_pages"] or 0,
                    "embedding_tokens": monthly_usage["embedding_tokens"] or 0,
                    "web_searches": monthly_usage["web_searches"] or 0,
                },
            },
        })
    
    return _view(request)


@csrf_exempt
@require_POST
def usage_sync_view(request):
    """POST /api/v1/usage/sync/ — Accept desktop usage sync.
    
    OCR, embeddings, and web search usage is already tracked in real-time
    by the proxy endpoints (_proxy_mistral_ocr, _proxy_siliconflow_embeddings,
    _proxy_web_search). This endpoint only acknowledges the sync to keep the
    desktop happy — no duplicate UsageLog records are created.
    """
    from .decorators import token_required

    @token_required
    def _view(request):
        return JsonResponse({"status": "ok", "records_created": 0})

    return _view(request)


# =============================================================================
# Billing endpoints (Desktop)
# =============================================================================

@require_GET
def billing_subscription_view(request):
    """GET /api/v1/billing/subscription/ — Get current subscription."""
    from .decorators import token_required
    
    @token_required
    def _view(request):
        user = request.user
        try:
            sub = Subscription.objects.select_related().get(user=user, status=Subscription.Status.ACTIVE)
            return JsonResponse({
                "plan": sub.plan,
                "status": sub.status,
                "license_key": sub.license_key,
                "start_date": sub.current_period_start.isoformat() if sub.current_period_start else None,
                "end_date": sub.current_period_end.isoformat() if sub.current_period_end else None,
                "auto_renew": False,
            })
        except Subscription.DoesNotExist:
            return JsonResponse({
                "plan": "free",
                "status": "none",
                "license_key": "",
                "start_date": None,
                "end_date": None,
                "auto_renew": False,
            })
    
    return _view(request)


@require_GET
def billing_credits_view(request):
    """GET /api/v1/billing/credits/ — Get credit balance.
    
    NOTE: Credit system removed — Cortex is fully BYOK.
    Users pay LLM providers directly. Subscription covers
    platform services only (web search, embeddings, OCR).
    Returns zero balance for backward compatibility.
    """
    from .decorators import token_required
    
    @token_required
    def _view(request):
        return JsonResponse({
            "balance": 0,
            "used_this_month": 0,
            "monthly_allocation": 0,
            "last_reset": None,
        })
    
    return _view(request)


@require_GET
def billing_history_view(request):
    """GET /api/v1/billing/history/ — Get payment history."""
    from .decorators import token_required
    from .models import Payment
    
    @token_required
    def _view(request):
        user = request.user
        payments = Payment.objects.filter(user=user).order_by("-created_at")[:20]
        
        history = []
        for p in payments:
            history.append({
                "id": p.pk,
                "amount": float(p.amount),
                "currency": p.currency,
                "status": p.status,
                "gateway": p.gateway,
                "created_at": p.created_at.isoformat(),
                "description": "",
            })
        
        return JsonResponse({"payments": history})
    
    return _view(request)


# =============================================================================
# Download & Version Check Endpoints
# =============================================================================


@csrf_exempt
@require_GET
def download_version_view(request):
    """GET /api/v1/download/version/ — Returns latest release info as JSON.
    
    Used by the download page to show current version, file size, and SHA-256.
    Also used by the desktop update checker.
    """
    from .models import Release
    
    release = Release.latest_active()
    if release:
        return JsonResponse({
            "version": release.version,
            "filename": release.file.name.split("/")[-1] if release.file else f"Cortex_Setup_v{release.version}.exe",
            "size": release.file_size,
            "sha256": release.sha256,
            "force_update": release.force_update,
            "notes": release.release_notes,
        })
    return JsonResponse({"version": "0.0.0", "filename": "", "size": 0, "sha256": "", "force_update": False, "notes": ""})


@csrf_exempt
@require_GET
def download_latest_view(request):
    """GET /api/v1/download/latest/ — Streams the latest .exe installer.
    
    Increments download count and returns the binary with proper headers.
    """
    from .models import Release
    
    release = Release.latest_active()
    if not release or not release.file:
        return JsonResponse({"error": "no_release_available"}, status=404)
    
    # Increment download counter
    Release.objects.filter(pk=release.pk).update(
        downloads_count=db_models.F("downloads_count") + 1
    )

    # Per-download event: WHO downloaded WHAT version (admin panel Downloads
    # page). User comes from the website session when signed in; anonymous
    # downloads log IP + user agent only. Never blocks the download itself.
    #
    # DEDUPE: browsers issue multiple range/resume requests for a 300MB file
    # (one iPhone logged the same download 5x within a minute). Same IP +
    # same version within an hour = ONE download, not a new event.
    try:
        from datetime import timedelta
        from .models import DownloadEvent
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")
        already_counted = DownloadEvent.objects.filter(
            ip_address=ip or None,
            version=release.version,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).exists()
        if not already_counted:
            DownloadEvent.objects.create(
                user=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
                release=release,
                version=release.version,
                ip_address=ip or None,
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:300],
            )
    except Exception:
        logger.warning("DownloadEvent logging failed", exc_info=True)

    filename = release.file.name.split("/")[-1]
    response = FileResponse(
        release.file.open("rb"),
        content_type="application/octet-stream",
        as_attachment=True,
        filename=filename,
    )
    response["Content-Length"] = release.file_size
    return response


@csrf_exempt
@require_GET
def version_check_view(request):
    """GET /api/v1/version/check/?current=2.5.0 — Desktop update checker.
    
    Returns the latest version info. Desktop compares its version 
    to decide whether to show an update notification.
    
    If force_update=True, the desktop IDE should BLOCK usage.
    """
    from .models import Release
    
    current = request.GET.get("current", "0.0.0")
    release = Release.latest_active()
    
    if not release:
        return JsonResponse({
            "update_available": False,
            "current_version": current,
            "latest_version": "0.0.0",
        })
    
    def parse(v):
        try:
            return tuple(int(x) for x in v.replace("v", "").split("."))
        except (ValueError, AttributeError):
            return (0, 0, 0)
    
    update_available = parse(release.version) > parse(current)
    
    return JsonResponse({
        "update_available": update_available,
        "current_version": current,
        "latest_version": release.version,
        "force": release.force_update and update_available,
        "url": f"{settings.CORTEX_SERVER_URL}/api/v1/download/latest/",
        "size": release.file_size,
        "sha256": release.sha256,
        "notes": release.release_notes,
    })
