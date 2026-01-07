from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('icons', '0001_initial'),
        ('prayers', '0007_merge_0006_feastprayer_0006_prayer_video'),
    ]

    operations = [
        migrations.AddField(
            model_name='prayerrequest',
            name='icon',
            field=models.ForeignKey(
                blank=True,
                help_text='Optional fallback icon for this prayer request (used when no custom image is uploaded)',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='prayer_requests',
                to='icons.icon',
            ),
        ),
    ]


