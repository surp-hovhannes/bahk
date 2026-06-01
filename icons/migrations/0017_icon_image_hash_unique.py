"""Add partial unique constraint on Icon.image_hash to prevent duplicate uploads."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('icons', '0004_icon_image_hash_icon_phash'),
    ]

    operations = [
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