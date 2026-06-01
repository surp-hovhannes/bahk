#!/usr/bin/env python
"""Find duplicate icons by phash."""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bahk.settings')
sys.path.insert(0, '/app')
django.setup()

from icons.models import Icon

# Find phash duplicates (excluding blank phash which means corrupt/missing images)
dupes = (
    Icon.objects
    .exclude(phash='')
    .values('phash')
    .annotate(count=django.db.models.Count('id'))
    .filter(count__gt=1)
    .order_by('-count')
)

total = 0
for row in dupes:
    print(f"phash={row['phash']} count={row['count']}")
    total += row['count']

print(f"\nTotal duplicate icons: {total}")
print(f"Unique phash values with duplicates: {dupes.count()}")