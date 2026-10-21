"""Print a privacy-suppressed aggregate growth report."""

import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from events.growth_cohorts import build_report


class Command(BaseCommand):
    help = ('Read-only week/month/all signup cohorts with mature denominators and small-cell suppression. '
            'Comparing signups-before cutoffs, as-of dates, or granularities for the same data can narrow '
            'small cells through differencing; review before publishing together.')

    def add_arguments(self, parser):
        parser.add_argument('--as-of', required=True, type=date.fromisoformat)
        parser.add_argument('--signups-before', type=date.fromisoformat,
                            help='Restrict signups and exclusions to before this UTC date (exclusive; must be <= --as-of). '
                                 'For mature PRD figures use --as-of <today> --granularity all '
                                 '--signups-before <as_of minus 31 days>; results describe only older signups. '
                                 'Maturity still uses --as-of; other suppression rules still apply.')
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
            signups_before = options['signups_before']
            if isinstance(signups_before, str):
                signups_before = date.fromisoformat(signups_before)
            report = build_report(as_of, options['min_cell'], options['granularity'], signups_before)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        content = json.dumps(report, indent=2)
        if options['format'] == 'md':
            content = '# Growth cohorts\n\nAggregate report (exact-day, not cumulative).\n\n```json\n' + content + '\n```'
        self.stdout.write(content)
