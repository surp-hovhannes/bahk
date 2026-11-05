"""Inspect or process pending Apple revocations without exposing credentials."""
import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from hub.models import PendingAppleRevocation
from hub.tasks.social_auth_tasks import MAX_BATCH_SIZE, process_pending_apple_revocations_task


class Command(BaseCommand):
    help = "Inspect pending Apple revocations (--status), or process a bounded due batch."

    def add_arguments(self, parser):
        parser.add_argument("--status", action="store_true", help="Read-only aggregate counts; no network calls.")
        parser.add_argument("--limit", type=int, default=MAX_BATCH_SIZE)

    def handle(self, *args, **options):
        pending = PendingAppleRevocation.objects.all()
        if options["status"]:
            result = {
                "pending": pending.count(),
                "due": pending.filter(next_attempt_at__lte=timezone.now()).count(),
                "failed_before": pending.filter(attempts__gt=0).count(),
            }
        else:
            result = process_pending_apple_revocations_task(limit=options["limit"])
        self.stdout.write(json.dumps(result, sort_keys=True))
