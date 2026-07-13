"""
Management command: cleanup_crash_reports

Deletes crash reports older than N days (default: 90).
Run via cron or scheduled task:

  # Daily at 3 AM
  0 3 * * * cd /path/to/cortex && python manage.py cleanup_crash_reports

Or use Django's built-in cron alternatives (django-crontab, celery beat, etc.).
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from api.models import CrashReport


class Command(BaseCommand):
    help = "Delete crash reports older than N days to free storage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=getattr(settings, "TELEMETRY_RETENTION_DAYS", 90),
            help=f"Delete reports older than N days (default: {getattr(settings, 'TELEMETRY_RETENTION_DAYS', 90)})",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many would be deleted without actually deleting",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]

        from django.utils import timezone
        from datetime import timedelta

        cutoff = timezone.now() - timedelta(days=days)
        count = CrashReport.objects.filter(created_at__lt=cutoff).count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"DRY RUN: Would delete {count} reports older than {days} days")
            )
        else:
            deleted, _ = CrashReport.objects.filter(created_at__lt=cutoff).delete()
            self.stdout.write(
                self.style.SUCCESS(f"Deleted {deleted} crash reports older than {days} days")
            )

        # Show remaining count
        remaining = CrashReport.objects.count()
        self.stdout.write(f"Total crash reports remaining: {remaining}")
