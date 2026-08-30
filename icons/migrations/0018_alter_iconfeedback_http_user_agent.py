"""Align IconFeedback.http_user_agent migration state with the model.

The Python default was added with IconFeedback, but migration 0003 omitted it.
This is deliberately state-only: Django defaults are application-side, so no
database schema or existing row needs to change.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("icons", "0017_icon_image_hash_unique"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="iconfeedback",
                    name="http_user_agent",
                    field=models.TextField(blank=True, default=""),
                ),
            ],
        ),
    ]
