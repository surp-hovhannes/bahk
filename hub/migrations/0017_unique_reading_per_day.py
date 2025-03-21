# Generated by Django 4.2.11 on 2024-11-10 16:10

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0016_remove_fast_unique_name_church_fast_year_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Reading',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('book', models.CharField(max_length=64)),
                ('start_chapter', models.IntegerField(verbose_name='Start Chapter')),
                ('start_verse', models.IntegerField(verbose_name='Start Verse')),
                ('end_chapter', models.IntegerField(help_text='May be same as start chapter', verbose_name='End Chapter')),
                ('end_verse', models.IntegerField(help_text='May be same as end verse', verbose_name='End Verse')),
                ('day', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='readings', to='hub.day')),
            ],
        ),
        migrations.AddConstraint(
            model_name='reading',
            constraint=models.UniqueConstraint(fields=('day', 'book', 'start_chapter', 'start_verse', 'end_chapter', 'end_verse'), name='unique_reading_per_day'),
        ),
    ]
