# Generated by Django 4.2.11 on 2024-10-16 18:58

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0015_alter_day_fast'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='fast',
            name='unique_name_church',
        ),
        migrations.AddField(
            model_name='fast',
            name='year',
            field=models.IntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(2024), django.core.validators.MaxValueValidator(3000)]),
        ),
        migrations.AddConstraint(
            model_name='fast',
            constraint=models.UniqueConstraint(fields=('name', 'church', 'year'), name='unique_name_church_year'),
        ),
    ]
