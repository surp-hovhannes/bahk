# Generated by Django 4.2.11 on 2024-07-21 03:58

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0012_auto_20240720_2053'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='day',
            name='fasts',
        ),
    ]
