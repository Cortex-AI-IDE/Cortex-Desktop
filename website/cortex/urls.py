from django.urls import path
from django.views.generic import RedirectView
from django.templatetags.static import static as static_url

from .views import (
    ChangelogView,
    DocsView,
    DownloadView,
    EulaView,
    IndexView,
    PricingView,
    PrivacyView,
    TermsView,
    google_verify,
    robots_txt,
    sitemap_xml,
)

app_name = "cortex"

urlpatterns = [
    path("", IndexView.as_view(), name="index"),
    path("pricing/", PricingView.as_view(), name="pricing"),
    path("download/", DownloadView.as_view(), name="download"),
    path("docs/", DocsView.as_view(), name="docs"),
    path("changelog/", ChangelogView.as_view(), name="changelog"),
    path("privacy/", PrivacyView.as_view(), name="privacy"),
    path("terms/", TermsView.as_view(), name="terms"),
    path("license/", EulaView.as_view(), name="eula"),
    # SEO
    path("robots.txt", robots_txt, name="robots"),
    # Browsers/crawlers request this exact path regardless of <link> tags
    path("favicon.ico", RedirectView.as_view(
        url=static_url("cortex/img/favicon.ico"), permanent=True), name="favicon"),
    path(
        "googlea2045ca96184af8b.html",
        google_verify,
        name="google-verify",
    ),
    path("sitemap.xml", sitemap_xml, name="sitemap"),
]
