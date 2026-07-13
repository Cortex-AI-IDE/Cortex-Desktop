from django.http import HttpResponse
from django.views.generic import TemplateView

SITE_URL = "https://cortex-ide.app"

# Public, indexable pages: (path, changefreq, priority)
_SITEMAP_PAGES = [
    ("/",          "weekly",  "1.0"),
    ("/download/", "weekly",  "0.9"),
    ("/pricing/",  "weekly",  "0.9"),
    ("/docs/",     "weekly",  "0.8"),
    ("/changelog/", "weekly", "0.6"),
    ("/privacy/",  "monthly", "0.3"),
    ("/terms/",    "monthly", "0.3"),
    ("/license/",  "monthly", "0.3"),
]


def google_verify(request):
    """Google Search Console site-ownership verification file."""
    return HttpResponse(
        "google-site-verification: googlea2045ca96184af8b.html",
        content_type="text/html",
    )


def robots_txt(request):
    """robots.txt — allow public pages, block private areas, point to sitemap.

    /media/ is blocked so crawlers never pull the ~300 MB installer binary
    on every crawl (bandwidth); the /download/ PAGE stays fully indexable.
    """
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /account/",
        "Disallow: /admin-panel/",
        "Disallow: /ops/",
        "Disallow: /api/",
        "Disallow: /payment/",
        "Disallow: /auth/",
        "Disallow: /media/",
        "",
        f"Sitemap: {SITE_URL}/sitemap.xml",
        "",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def sitemap_xml(request):
    """sitemap.xml — standard urlset for Google/Bing."""
    urls = "".join(
        f"  <url>\n"
        f"    <loc>{SITE_URL}{path}</loc>\n"
        f"    <changefreq>{freq}</changefreq>\n"
        f"    <priority>{prio}</priority>\n"
        f"  </url>\n"
        for path, freq, prio in _SITEMAP_PAGES
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}"
        "</urlset>\n"
    )
    return HttpResponse(xml, content_type="application/xml")


class IndexView(TemplateView):
    """Cortex landing page."""
    template_name = "cortex/index.html"


class PricingView(TemplateView):
    """Public pricing page — shows plans and prices. No payment buttons."""
    template_name = "cortex/pricing.html"


class DownloadView(TemplateView):
    """Windows download page."""
    template_name = "cortex/download.html"


class DocsView(TemplateView):
    """Product documentation — API keys, modes, agentic loop, memory, tips."""
    template_name = "cortex/docs.html"


class ChangelogView(TemplateView):
    """Public release notes — user-facing changes only, one section per version."""
    template_name = "cortex/changelog.html"


class PrivacyView(TemplateView):
    template_name = "cortex/privacy.html"


class TermsView(TemplateView):
    template_name = "cortex/terms.html"


class EulaView(TemplateView):
    template_name = "cortex/eula.html"
