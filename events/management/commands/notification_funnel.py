"""Print notification funnel aggregates without changing telemetry configuration."""
import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from events.notification_funnel import build_report


class Command(BaseCommand):
    help = 'Read-only notification funnel. Review differencing risks before publishing overlapping reports.'

    def add_arguments(self, parser):
        parser.add_argument('--as-of', required=True, type=date.fromisoformat)
        parser.add_argument('--since-days', type=int, default=30)
        parser.add_argument('--format', choices=['json', 'text'], default='json')
        parser.add_argument('--min-cell', type=int, default=10)

    def handle(self, *args, **options):
        try:
            as_of = options['as_of']
            if isinstance(as_of, str):
                as_of = date.fromisoformat(as_of)
            report = build_report(as_of, options['since_days'], options['min_cell'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        content = json.dumps(report, indent=2)
        if options['format'] == 'text':
            content = 'Notification funnel — accepted by provider\n' + content
        self.stdout.write(content)
