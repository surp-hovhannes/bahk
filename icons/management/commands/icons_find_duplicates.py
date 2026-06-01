"""Find duplicate icon groups by perceptual hash."""
from django.core.management.base import BaseCommand
from django.db.models import Count

from icons.models import Icon
from icons.utils import icon_association_count_expression


class Command(BaseCommand):
    """List duplicate icon groups."""

    help = 'Find duplicate icons grouped by pHash.'

    def handle(self, *args, **options):
        groups = list(
            Icon.objects.exclude(phash='')
            .values('phash')
            .annotate(icon_count=Count('id'))
            .filter(icon_count__gt=1)
            .order_by('phash')
        )

        total_icons = 0
        merge_candidates = 0

        for index, group in enumerate(groups, start=1):
            icons = list(
                Icon.objects.filter(phash=group['phash'])
                .select_related('church')
                .annotate(association_count=icon_association_count_expression())
                .order_by('-association_count', 'created_at', 'pk')
            )
            total_icons += len(icons)
            canonical = icons[0] if icons else None

            self.stdout.write(
                f"Group {index}: phash={group['phash']} count={group['icon_count']}"
            )
            for icon in icons:
                is_candidate = (
                    canonical is not None
                    and icon.pk != canonical.pk
                    and icon.association_count == 0
                )
                if is_candidate:
                    merge_candidates += 1
                candidate_label = ' MERGE CANDIDATE' if is_candidate else ''
                self.stdout.write(
                    f"  id={icon.pk} title={icon.title!r} "
                    f"created_at={icon.created_at.isoformat()} "
                    f"associations={icon.association_count}{candidate_label}"
                )

        self.stdout.write(
            f"Total: duplicate icons={total_icons}, groups={len(groups)}, "
            f"merge candidates={merge_candidates}"
        )
