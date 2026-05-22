# Generated manually for FastIntention model

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('hub', '0050_add_fast_designation_to_feast'),
    ]

    operations = [
        migrations.CreateModel(
            name='FastIntention',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.CharField(blank=True, default='', help_text='The intention text (max 280 characters)', max_length=280)),
                ('is_public', models.BooleanField(default=False, help_text='Whether this intention is visible to other fast participants')),
                ('is_active', models.BooleanField(default=True, help_text='Soft-delete flag; set to False when user leaves the fast')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('fast', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='intentions', to='hub.fast')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='fast_intentions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Fast Intention',
                'verbose_name_plural': 'Fast Intentions',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='fastintention',
            constraint=models.UniqueConstraint(condition=models.Q(('is_active', True)), fields=('user', 'fast'), name='one_active_intention_per_user_fast'),
        ),
        migrations.AddIndex(
            model_name='fastintention',
            index=models.Index(fields=['fast', 'is_active', 'is_public'], name='hub_fastint_fast_id_7f8e1e_idx'),
        ),
        migrations.AddIndex(
            model_name='fastintention',
            index=models.Index(fields=['user', 'fast'], name='hub_fastint_user_id_3a2b1c_idx'),
        ),
    ]
