"""
adminpanel/views.py — Cortex Admin Panel Views
=================================================

Superuser-only admin panel for managing users, subscriptions,
payments, usage logs, and IDE releases.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Count, Sum, Q
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.contrib import messages

from api.models import Subscription, UsageLog, Payment, Release, DownloadEvent

User = get_user_model()

PER_PAGE = 100


def superuser_required(view_func):
    """Decorator: only superusers can access admin panel views."""
    return user_passes_test(lambda u: u.is_superuser, login_url="account:login")(view_func)


def _paginate(request, queryset, per_page=PER_PAGE):
    """Paginate a queryset and return (page_obj, page_range_for_template)."""
    paginator = Paginator(queryset, per_page)
    page = request.GET.get("page", 1)
    try:
        page_obj = paginator.page(page)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    total = paginator.num_pages
    current = page_obj.number
    start = max(1, current - 2)
    end = min(total, current + 2)
    page_range = list(range(start, end + 1))

    return page_obj, page_range


# ═══════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════

@superuser_required
def dashboard(request):
    """Admin panel dashboard with overview stats."""
    today = timezone.now()
    this_month = today.replace(day=1)

    recent_subs = Subscription.objects.select_related("user").order_by("-created_at")[:10]

    context = {
        "total_users": User.objects.count(),
        "total_subscriptions": Subscription.objects.count(),
        "active_subscriptions": Subscription.objects.filter(status="active").count(),
        "total_payments": Payment.objects.filter(status="completed").count(),
        "revenue_usd": Payment.objects.filter(status="completed", gateway="paypal").aggregate(
            total=Sum("amount")
        )["total"] or 0,
        "users_this_month": User.objects.filter(date_joined__gte=this_month).count(),
        "recent_subscriptions": recent_subs,
        "recent_payments": Payment.objects.filter(status="completed").order_by("-created_at")[:5],
        "latest_release": Release.latest_active(),
        # UNIQUE downloads (distinct IP per version) — raw events include
        # browser range/resume duplicates for the 300MB installer
        "total_downloads": DownloadEvent.objects.values("ip_address", "version").distinct().count(),
        "downloads_this_month": DownloadEvent.objects.filter(created_at__gte=this_month)
                                .values("ip_address", "version").distinct().count(),
        # Which app versions are users still running (from X-Cortex-Version)
        "version_dist": (
            User.objects.exclude(last_seen_version="")
            .values("last_seen_version")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        ),
    }
    return render(request, "adminpanel/dashboard.html", context)


# ═══════════════════════════════════════════════════════════════════════
# Subscriptions
# ═══════════════════════════════════════════════════════════════════════

@superuser_required
def subscription_list(request):
    """List all subscriptions with filters and pagination."""
    subs = Subscription.objects.select_related("user").order_by("-created_at")

    plan = request.GET.get("plan", "")
    status = request.GET.get("status", "")
    search = request.GET.get("search", "")

    if plan:
        subs = subs.filter(plan=plan)
    if status:
        subs = subs.filter(status=status)
    if search:
        subs = subs.filter(Q(email__icontains=search) | Q(license_key__icontains=search))

    page_obj, page_range = _paginate(request, subs)

    return render(request, "adminpanel/subscriptions.html", {
        "page_obj": page_obj,
        "page_range": page_range,
        "plan_filter": plan,
        "status_filter": status,
        "search": search,
    })


@superuser_required
def subscription_detail(request, sub_id):
    """Detail view for a single subscription."""
    sub = get_object_or_404(Subscription.objects.select_related("user"), pk=sub_id)
    usage_logs = UsageLog.objects.filter(subscription=sub).order_by("-created_at")[:50]
    payments = Payment.objects.filter(
        Q(paypal_order_id=sub.paypal_order_id) | Q(razorpay_order_id=sub.razorpay_order_id)
    ).order_by("-created_at")

    return render(request, "adminpanel/subscription_detail.html", {
        "subscription": sub,
        "usage_logs": usage_logs,
        "payments": payments,
    })


# ═══════════════════════════════════════════════════════════════════════
# Users
# ═══════════════════════════════════════════════════════════════════════

@superuser_required
def user_list(request):
    """List all users with pagination."""
    users = User.objects.annotate(
        has_sub=Count("subscription"),
    ).order_by("-date_joined")

    search = request.GET.get("search", "")
    if search:
        users = users.filter(Q(username__icontains=search) | Q(email__icontains=search))

    page_obj, page_range = _paginate(request, users)

    return render(request, "adminpanel/users.html", {
        "page_obj": page_obj,
        "page_range": page_range,
        "search": search,
    })


# ═══════════════════════════════════════════════════════════════════════
# Release Management
# ═══════════════════════════════════════════════════════════════════════

@superuser_required
def release_list(request):
    """List all releases with pagination."""
    releases = Release.objects.all().order_by("-created_at")
    page_obj, page_range = _paginate(request, releases)
    return render(request, "adminpanel/releases.html", {
        "page_obj": page_obj,
        "page_range": page_range,
    })


@superuser_required
def release_create(request):
    """Create a new release (upload .exe)."""
    if request.method == "POST":
        version = request.POST.get("version", "").strip()
        release_notes = request.POST.get("release_notes", "")
        force_update = request.POST.get("force_update") == "on"
        uploaded_file = request.FILES.get("file")

        if not version:
            messages.error(request, "Version is required.")
            return render(request, "adminpanel/release_form.html")

        if Release.objects.filter(version=version).exists():
            messages.error(request, f"Version {version} already exists.")
            return render(request, "adminpanel/release_form.html")

        release = Release(
            version=version,
            release_notes=release_notes,
            force_update=force_update,
        )
        if uploaded_file:
            release.file = uploaded_file
        release.save()
        # Deactivate all older releases — only the latest stays active
        Release.objects.filter(is_active=True).exclude(pk=release.pk).update(is_active=False)
        messages.success(request, f"Release v{version} published. All older releases deactivated.")
        return redirect("adminpanel:releases")

    return render(request, "adminpanel/release_form.html")


@superuser_required
def download_list(request):
    """Download tracking: who downloaded which installer version, when."""
    events = DownloadEvent.objects.select_related("user", "release").order_by("-created_at")

    version = request.GET.get("version", "")
    search = request.GET.get("search", "")
    if version:
        events = events.filter(version=version)
    if search:
        events = events.filter(
            Q(user__email__icontains=search)
            | Q(user__username__icontains=search)
            | Q(ip_address__icontains=search)
        )

    page_obj, page_range = _paginate(request, events)

    # Per-version downloads: UNIQUE (distinct IPs) is the headline number;
    # raw event count kept alongside for auditing.
    per_version = (
        DownloadEvent.objects.values("version")
        .annotate(count=Count("id"),
                  unique=Count("ip_address", distinct=True))
        .order_by("-unique")
    )
    versions = list(
        DownloadEvent.objects.values_list("version", flat=True).distinct().order_by("-version")
    )

    return render(request, "adminpanel/downloads.html", {
        "page_obj": page_obj,
        "page_range": page_range,
        "per_version": per_version,
        "versions": versions,
        "version_filter": version,
        "search": search,
        "total_events": DownloadEvent.objects.count(),
        "unique_downloads": DownloadEvent.objects.values("ip_address", "version")
                            .distinct().count(),
        "signed_in_events": DownloadEvent.objects.filter(user__isnull=False).count(),
        "unique_users": DownloadEvent.objects.filter(user__isnull=False)
                        .values("user").distinct().count(),
    })


@superuser_required
def release_toggle(request, release_id):
    """Toggle release active status."""
    release = get_object_or_404(Release, pk=release_id)
    release.is_active = not release.is_active
    release.save(update_fields=["is_active"])
    return redirect("adminpanel:releases")
