# Generated by Django 4.2.11 on 2024-11-20 11:56

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="devicetoken",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="device_tokens",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="devicetoken",
            name="device_type",
            field=models.CharField(
                choices=[("ios", "iOS"), ("android", "Android"), ("web", "Web")],
                max_length=10,
                null=True,
            ),
        ),
    ]