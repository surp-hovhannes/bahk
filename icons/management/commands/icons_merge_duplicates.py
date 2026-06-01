"""Merge safe duplicate icon records."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from icons.models import Icon


ASSOCIATION_COUNT = (
    Count('prayers', distinct=True)
    + Count('prayer_sets', distinct=True)
    + Count('prayer_requests', distinct=True)
    + Count('feasts', distinct=True)
)


class Command(BaseCommand):
    """Delete unreferenced duplicate icons."""

    help = 'Merge unassigned duplicate icons grouped by pHash.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report merge actions without deleting records.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of duplicate groups fetched per database batch.',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        dry_run = options['dry_run']
        if batch_size < 1:
            raise CommandError('--batch-size must be greater than 0.')

        groups = (
            Icon.objects.exclude(phash='')
            .values('phash')
            .annotate(icon_count=Count('id'))
            .filter(icon_count__gt=1)
            .order_by('phash')
        )

        merged = 0
        skipped = 0
        processed = 0

        for group in groups.iterator(chunk_size=batch_size):
            processed += 1
            with transaction.atomic():
                icons = list(
                    Icon.objects.select_for_update()
                    .filter(phash=group['phash'])
                    .annotate(association_count=ASSOCIATION_COUNT)
                    .order_by('-association_count', 'created_at', 'pk')
                )
                if len(icons) < 2:
                    continue

                canonical = icons[0]
                for duplicate in icons[1:]:
                    if duplicate.association_count > 0:
                        skipped += 1
                        self.stderr.write(
                            f"Skipped icon {duplicate.pk} ({duplicate.title}) "
                            f"with {duplicate.association_count} associations"
                        )
                        continue

                    if dry_run:
                        merged += 1
                        self.stdout.write(
                            f"Would merge icon {duplicate.pk} ({duplicate.title}) "
                            f"into canonical icon {canonical.pk}"
                        )
                        continue

                    duplicate_pk = duplicate.pk
                    duplicate_title = duplicate.title
                    duplicate.delete()
                    merged += 1
                    self.stdout.write(
                        f"Merged icon {duplicate_pk} ({duplicate_title}) "
                        f"into canonical icon {canonical.pk}"
                    )

        self.stdout.write(
            f"Merged {merged}, Skipped {skipped}, Groups {processed}"
        )
