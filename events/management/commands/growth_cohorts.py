"""Print a privacy-suppressed aggregate growth report."""

import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from events.growth_cohorts import build_report


class Command(BaseCommand):
    help = ('Read-only week/month/all signup cohorts with mature denominators and small-cell suppression. '
            'Comparing granularities or as-of dates can reveal small cells; review before publishing together.')

    def add_arguments(self, parser):
        parser.add_argument('--as-of', required=True, type=date.fromisoformat)
        parser.add_argument('--format', choices=['json', 'md'], default='json')
        parser.add_argument('--granularity', choices=['week', 'month', 'all'], default='week',
                            help='UTC signup weeks (default), calendar months, or all eligible signups.')
        parser.add_argument('--min-cell', type=int, default=10)
        parser.add_argument('--unsafe-min-cell', action='store_true',
                            help='Allow thresholds below 10 for synthetic testing only.')

    def handle(self, *args, **options):
        if options['min_cell'] < 10 and not options['unsafe_min_cell']:
            raise CommandError('--min-cell must be at least 10 unless --unsafe-min-cell is passed')
        try:
            as_of = options['as_of']
            if isinstance(as_of, str):
                as_of = date.fromisoformat(as_of)
            report = build_report(as_of, options['min_cell'], options['granularity'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        content = json.dumps(report, indent=2)
        if options['format'] == 'md':
            content = '# Growth cohorts\n\nAggregate report (exact-day, not cumulative).\n\n```json\n' + content + '\n```'
        self.stdout.write(content)
