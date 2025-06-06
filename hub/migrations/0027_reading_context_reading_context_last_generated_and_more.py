# Generated by Django 4.2.11 on 2025-04-26 03:22

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0026_profile_receive_promotional_emails'),
    ]

    operations = [
        migrations.AddField(
            model_name='reading',
            name='context',
            field=models.TextField(blank=True, help_text='AI-generated contextual introduction', null=True),
        ),
        migrations.AddField(
            model_name='reading',
            name='context_last_generated',
            field=models.DateTimeField(blank=True, help_text='Timestamp of the last context generation', null=True),
        ),
        migrations.AddField(
            model_name='reading',
            name='context_thumbs_down',
            field=models.PositiveIntegerField(default=0, help_text='Number of thumbs-down votes for the context'),
        ),
        migrations.AddField(
            model_name='reading',
            name='context_thumbs_up',
            field=models.PositiveIntegerField(default=0, help_text='Number of thumbs-up votes for the context'),
        ),
    ]
