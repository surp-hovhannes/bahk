"""Parent dispatcher for icon management utilities."""
import argparse

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Dispatch icon utility subcommands."""

    help = "Icon management utilities"
    subcommands = ['backfill', 'find-duplicates', 'merge-duplicates']

    command_map = {
        'backfill': 'icons_backfill',
        'find-duplicates': 'icons_find_duplicates',
        'merge-duplicates': 'icons_merge_duplicates',
    }

    def add_arguments(self, parser):
        parser.add_argument(
            'subcommand',
            nargs='?',
            choices=self.subcommands,
            help='Icon utility to run.',
        )
        parser.add_argument(
            'subcommand_args',
            nargs=argparse.REMAINDER,
            help='Arguments passed to the selected icon utility.',
        )

    def handle(self, *args, **options):
        subcommand = options.get('subcommand')
        if not subcommand:
            self.print_help('manage.py', 'icons')
            return

        command_name = self.command_map.get(subcommand)
        if not command_name:
            raise CommandError(f'Unknown icons subcommand: {subcommand}')

        call_command(
            command_name,
            *options.get('subcommand_args', []),
            stdout=self.stdout,
            stderr=self.stderr,
        )
