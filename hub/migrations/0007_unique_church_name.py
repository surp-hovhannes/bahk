# Generated by Django 4.2.11 on 2024-05-20 06:18

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0006_unique_culmination_feast_date_per_church'),
    ]

    operations = [
        migrations.AlterField(
            model_name='church',
            name='name',
            field=models.CharField(max_length=128, unique=True),
        ),
    ]
