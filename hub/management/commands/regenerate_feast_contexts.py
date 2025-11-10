"""Regenerate feast contexts to apply new parsing logic."""
import logging

from django.core.management.base import BaseCommand

from hub.models import Feast
from hub.tasks import generate_feast_context_task


class Command(BaseCommand):
    help = "Regenerate feast contexts for all feasts or a specific date range"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Regenerate contexts for all feasts",
        )
        parser.add_argument(
            "--feast-id",
            type=int,
            help="Regenerate context for a specific feast ID",
        )

    def handle(self, *args, **options):
        if options.get("feast_id"):
            # Regenerate for specific feast
            try:
                feast = Feast.objects.get(pk=options["feast_id"])
                self.stdout.write(f"Regenerating context for feast: {feast}")
                generate_feast_context_task.delay(feast.id, force_regeneration=True)
                self.stdout.write(self.style.SUCCESS(f"✓ Queued regeneration for feast {feast.id}"))
            except Feast.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Feast with ID {options['feast_id']} not found"))
                return
        
        elif options.get("all"):
            # Regenerate for all feasts
            feasts = Feast.objects.all()
            count = feasts.count()
            
            if count == 0:
                self.stdout.write(self.style.WARNING("No feasts found in database"))
                return
            
            self.stdout.write(f"Regenerating contexts for {count} feasts...")
            
            for feast in feasts:
                generate_feast_context_task.delay(feast.id, force_regeneration=True)
                self.stdout.write(f"  ✓ Queued: {feast}")
            
            self.stdout.write(self.style.SUCCESS(f"\n✓ Queued regeneration for {count} feasts"))
        
        else:
            self.stdout.write(self.style.ERROR(
                "Please specify either --all to regenerate all feasts or --feast-id <ID> for a specific feast"
            ))

