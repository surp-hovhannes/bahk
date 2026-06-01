"""Management command to find duplicate icons by phash."""
from django.core.management.base import BaseCommand
from django.db.models import Count
from icons.models import Icon


class Command(BaseCommand):
    help = "Find duplicate icons grouped by phash"

    def handle(self):
        dupes = (
            Icon.objects
            .exclude(phash='')
            .values('phash')
            .annotate(count=Count('id'))
            .filter(count__gt=1)
            .order_by('-count')
        )

        total = 0
        for row in dupes:
            self.stdout.write(f"phash={row['phash']} count={row['count']}")
            total += row['count']

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTotal duplicate icons: {total}\n"
                f"Unique phash values with duplicates: {dupes.count()}"
            )
        )