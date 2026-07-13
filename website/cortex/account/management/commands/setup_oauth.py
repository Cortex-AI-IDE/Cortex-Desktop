"""
Management command to set up OAuth social applications from environment variables.
Run: python manage.py setup_oauth
"""
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp
from django.conf import settings
import os


class Command(BaseCommand):
    help = "Set up OAuth social applications from environment variables"

    def handle(self, *args, **options):
        site, created = Site.objects.get_or_create(
            id=settings.SITE_ID,
            defaults={"domain": "cortex-ide.app", "name": "Cortex AI IDE"}
        )

        if site.domain == "example.com":
            site.domain = "cortex-ide.app"
            site.name = "Cortex AI IDE"
            site.save()
            self.stdout.write(self.style.SUCCESS(f"Updated site domain to: {site.domain}"))

        # Setup Google OAuth
        google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
        google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

        if google_client_id and google_client_secret:
            google_app, created = SocialApp.objects.update_or_create(
                provider="google",
                defaults={
                    "name": "Google",
                    "client_id": google_client_id,
                    "secret": google_client_secret,
                }
            )
            google_app.sites.add(site)
            status = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{status} Google OAuth app"))
        else:
            self.stdout.write(self.style.WARNING("Google OAuth credentials not found in environment"))

        # Setup GitHub OAuth
        github_client_id = os.environ.get("GITHUB_CLIENT_ID")
        github_client_secret = os.environ.get("GITHUB_CLIENT_SECRET")

        if github_client_id and github_client_secret:
            github_app, created = SocialApp.objects.update_or_create(
                provider="github",
                defaults={
                    "name": "GitHub",
                    "client_id": github_client_id,
                    "secret": github_client_secret,
                }
            )
            github_app.sites.add(site)
            status = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{status} GitHub OAuth app"))
        else:
            self.stdout.write(self.style.WARNING("GitHub OAuth credentials not found in environment"))

        self.stdout.write(self.style.SUCCESS("\nOAuth setup complete!"))
