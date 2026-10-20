"""Print a privacy-suppressed aggregate growth report."""

import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from events.growth_cohorts import build_report


class Command(BaseCommand):
    help = 'Read-only weekly growth cohorts, with mature denominators and small-cell suppression.'

    def add_arguments(self, parser):
        parser.add_argument('--as-of', required=True, type=date.fromisoformat)
        parser.add_argument('--format', choices=['json', 'md'], default='json')
        parser.add_argument('--min-cell', type=int, default=10)

    def handle(self, *args, **options):
        try:
            as_of = options['as_of']
            if isinstance(as_of, str):
                as_of = date.fromisoformat(as_of)
            report = build_report(as_of, options['min_cell'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        content = json.dumps(report, indent=2)
        if options['format'] == 'md':
            content = '# Growth cohorts\n\nAggregate report (exact-day, not cumulative).\n\n```json\n' + content + '\n```'
        self.stdout.write(content)
