# Generated by Django 4.2.11 on 2024-11-15 19:51

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0002_alter_devicetoken_options_devicetoken_is_active"),
    ]

    operations = [
        migrations.AddField(
            model_name="devicetoken",
            name="device_type",
            field=models.CharField(
                choices=[("ios", "iOS"), ("android", "Android")],
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="devicetoken",
            name="last_used",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]