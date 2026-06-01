"""Backfill image footprint fields for existing icons."""
import logging

from django.core.management.base import BaseCommand, CommandError
from icons.models import Icon

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Populate image_hash and phash for existing Icon rows."""

    help = 'Backfill image_hash and phash for existing icons.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Compute and report pending updates without saving changes.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of rows fetched per database batch.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Recompute footprints even when both values are already present.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Optional maximum number of icons to scan.',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        limit = options.get('limit')
        dry_run = options['dry_run']
        force = options['force']

        if batch_size < 1:
            raise CommandError('--batch-size must be greater than 0.')
        if limit is not None and limit < 1:
            raise CommandError('--limit must be greater than 0.')

        queryset = Icon.objects.exclude(image='')
        if not force:
            # Only backfill rows missing image_hash.
            # Blank phash is a valid terminal state for corrupt images;
            # use --force to reattempt pHash for those rows.
            queryset = queryset.filter(image_hash='')
        queryset = queryset.order_by('pk')
        if limit is not None:
            queryset = queryset[:limit]

        scanned = 0
        skipped = 0
        updated = 0
        would_update = 0
        stale = 0
        phash_failures = 0
        storage_failures = 0

        for icon in queryset.iterator(chunk_size=batch_size):
            scanned += 1
            try:
                image_hash, phash = icon._compute_image_footprints()
            except Exception as exc:
                storage_failures += 1
                logger.exception(
                    'Could not compute footprints for Icon %s (%s)',
                    icon.pk,
                    icon.image.name,
                )
                self.stderr.write(
                    f'Icon {icon.pk} ({icon.image.name}): storage/read failure: {exc}'
                )
                continue

            if not phash:
                phash_failures += 1

            if icon.image_hash == image_hash and icon.phash == phash:
                skipped += 1
                continue

            if dry_run:
                would_update += 1
                continue

            # Use a conditional update to avoid:
            #   - overwriting footprints set by a newer concurrent upload.
            #   - triggering Icon.save() side effects (thumbnail work).
            rows = Icon.objects.filter(
                pk=icon.pk, image=icon.image.name
            ).update(image_hash=image_hash, phash=phash)
            if rows:
                updated += 1
            else:
                stale += 1

        summary = (
            f'scanned={scanned} skipped={skipped} '
            f'updated={updated} would_update={would_update} '
            f'stale={stale} '
            f'phash_failures={phash_failures} storage_failures={storage_failures}'
        )
        self.stdout.write(summary)
