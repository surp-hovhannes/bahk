# Generated by Django 4.2.11 on 2024-07-21 03:58

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0012_fast_m2m_to_fk_mapping'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='day',
            name='fasts',
        ),
    ]