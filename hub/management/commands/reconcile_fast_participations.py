"""Replay membership events after code deployment to cover the migration/code gap."""

from types import SimpleNamespace

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connections, transaction

from hub.models import Fast, Profile
from hub.services.fast_participation_backfill import backfill_fast_participation


class Command(BaseCommand):
    help = "Reconcile FastParticipation history from events/current membership; rerunnable, preserves unknown dates."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        alias = options["database"]
        with transaction.atomic(using=alias):
            # Match receiver lock order. Never overwrite joined_at/left_at of
            # existing periods; mark only missing membership endings uncertain.
            list(Fast.objects.using(alias).select_for_update().order_by("pk").values_list("pk", flat=True))
            list(Profile.objects.using(alias).select_for_update().order_by("pk").values_list("pk", flat=True))
            backfill_fast_participation(apps, SimpleNamespace(connection=connections[alias]))
        self.stdout.write(self.style.SUCCESS("Reconciled fast participation history; unknown timestamps remain NULL."))
