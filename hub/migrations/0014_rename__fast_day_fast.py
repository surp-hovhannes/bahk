# Generated by Django 4.2.11 on 2024-07-21 03:58

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0013_remove_day_fasts_m2m'),
    ]

    operations = [
        migrations.RenameField(
            model_name='day',
            old_name='_fast',
            new_name='fast',
        ),
    ]
