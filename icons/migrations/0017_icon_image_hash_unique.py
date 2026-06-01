"""Add partial unique constraint on Icon.image_hash to prevent duplicate uploads."""
from django.db import migrations, models


def clear_duplicate_image_hashes(apps, schema_editor):
    """Keep one row per existing image_hash before adding the unique index."""
    Icon = apps.get_model('icons', 'Icon')
    duplicate_hashes = (
        Icon.objects.exclude(image_hash='')
        .values('image_hash')
        .annotate(icon_count=models.Count('id'))
        .filter(icon_count__gt=1)
    )

    for duplicate in duplicate_hashes.iterator():
        icons = list(
            Icon.objects.filter(image_hash=duplicate['image_hash'])
            .order_by('created_at', 'pk')
            .values_list('pk', flat=True)
        )
        Icon.objects.filter(pk__in=icons[1:]).update(image_hash='')


def noop_reverse(apps, schema_editor):
    """Do not restore cleared hashes if the migration is reversed."""


class Migration(migrations.Migration):

    dependencies = [
        ('icons', '0004_icon_image_hash_icon_phash'),
    ]

    operations = [
        migrations.RunPython(
            clear_duplicate_image_hashes,
            reverse_code=noop_reverse,
        ),
        migrations.AddConstraint(
            model_name='icon',
            constraint=models.UniqueConstraint(
                fields=['image_hash'],
                name='unique_icon_image_hash',
                condition=models.Q(image_hash__gt=''),
                violation_error_message='An icon with this image hash already exists.',
            ),
        ),
    ]
