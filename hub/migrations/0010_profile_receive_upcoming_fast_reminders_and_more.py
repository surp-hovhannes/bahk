# Generated by Django 4.2.11 on 2024-06-25 05:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0009_alter_fast_culmination_feast_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="receive_upcoming_fast_reminders",
            field=models.BooleanField(default=True),
        ),
    ]
