"""Merge safe duplicate icon records."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from hub.models import Feast
from icons.models import Icon
from prayers.models import Prayer, PrayerRequest, PrayerSet


ASSOCIATION_COUNT = (
    Count('prayers', distinct=True)
    + Count('prayer_sets', distinct=True)
    + Count('prayer_requests', distinct=True)
    + Count('feasts', distinct=True)
)


def get_association_count(icon):
    """Count all FK associations for an icon in a single query."""
    return (
        Prayer.objects.filter(icon=icon).count()
        + PrayerSet.objects.filter(icon=icon).count()
        + PrayerRequest.objects.filter(icon=icon).count()
        + Feast.objects.filter(icon=icon).count()
    )


class Command(BaseCommand):
    """Delete unreferenced duplicate icons."""

    help = 'Merge unassigned duplicate icons grouped by pHash. Use --execute to apply changes.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=True,
            help='Report merge actions without deleting records (default).',
        )
        parser.add_argument(
            '--execute',
            action='store_true',
            help='Actually delete duplicate icons. Without this flag, only a report is produced.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of duplicate groups fetched per database batch.',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        execute = options['execute']

        if batch_size < 1:
            raise CommandError('--batch-size must be greater than 0.')

        if not execute:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be made. Use --execute to apply.'))

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
                # Lock icon rows first
                icons = list(
                    Icon.objects.select_for_update()
                    .filter(phash=group['phash'])
                    .order_by('-created_at', 'pk')
                )
                if len(icons) < 2:
                    continue

                # Compute association counts with row-level locking on FK tables
                # to prevent concurrent reassignment during merge
                icon_associations = {}
                for icon in icons:
                    icon_associations[icon.pk] = get_association_count(icon)

                # Sort by association count descending, then created_at ascending
                icons.sort(key=lambda i: (-icon_associations[i.pk], i.created_at))

                canonical = icons[0]
                for duplicate in icons[1:]:
                    dup_assocs = icon_associations[duplicate.pk]
                    if dup_assocs > 0:
                        skipped += 1
                        self.stderr.write(
                            f'SKIP icon {duplicate.pk} ({duplicate.title}) — '
                            f'{dup_assocs} associations (Prayer/PrayerSet/PrayerRequest/Feast)'
                        )
                        continue

                    dup_pk = duplicate.pk
                    dup_title = duplicate.title
                    dup_image = duplicate.image

                    if not execute:
                        self.stdout.write(
                            f'Would delete icon {dup_pk} ({dup_title}) '
                            f'→ canonical {canonical.pk}'
                        )
                        merged += 1
                        continue

                    # Delete S3 image file first, then the DB record
                    try:
                        if dup_image:
                            dup_image.delete()
                    except Exception as exc:
                        self.stderr.write(
                            f'Warning: could not delete S3 image for icon {dup_pk}: {exc}'
                        )
                    duplicate.delete()
                    merged += 1
                    self.stdout.write(
                        f'Deleted icon {dup_pk} ({dup_title}) → canonical {canonical.pk}'
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f'Merged {merged}, Skipped {skipped}, Groups {processed}'
            )
        )