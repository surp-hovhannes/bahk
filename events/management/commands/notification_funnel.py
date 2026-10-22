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
            lines = [f"Notification funnel — {report['since']} to {report['as_of']}",
                     f"Minimum cell: {report['min_cell']}", f"Data freshness: {report['data_freshness']}"]
            for label, metrics in [*report['groups'].items(), ('permission_prompts', report['permission_prompts'])]:
                lines.append(label)
                for name, cell in metrics.items():
                    value = (str(cell['count']) if 'count' in cell else
                             f"{cell['numerator']} / {cell['mature_denominator']} ({cell['percent']}%)")
                    lines.append(f"  {name}: {value}")
            lines.extend(report['caveats'])
            content = '\n'.join(lines)
        self.stdout.write(content)
