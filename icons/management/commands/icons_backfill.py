"""Shim for icon footprint backfill through the icons parent command."""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Run the existing backfill_icon_footprints command."""

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
        command_args = []
        if options['dry_run']:
            command_args.append('--dry-run')
        if options['force']:
            command_args.append('--force')
        if options.get('batch_size') is not None:
            command_args.extend(['--batch-size', str(options['batch_size'])])
        if options.get('limit') is not None:
            command_args.extend(['--limit', str(options['limit'])])

        call_command(
            'backfill_icon_footprints',
            *command_args,
            stdout=self.stdout,
            stderr=self.stderr,
        )
