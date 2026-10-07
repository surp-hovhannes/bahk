from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='Responsibility', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
            ('general', models.BooleanField(default=False)),
            ('crisis', models.BooleanField(default=False)),
            ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name='Review', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
            ('kind', models.CharField(max_length=20)),
            ('object_id', models.PositiveBigIntegerField()),
            ('outcome', models.CharField(max_length=30)),
            ('note', models.TextField(blank=True)),
            ('expected_outcome', models.CharField(max_length=80, blank=True)),
            ('regression_reference', models.CharField(max_length=200, blank=True)),
            ('signal_version', models.CharField(max_length=100)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('reviewer', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
        ], options={'ordering': ['-created_at', '-pk'], 'indexes': [models.Index(fields=['kind', 'object_id'], name='moderation__kind_c8a34e_idx')]}),
    ]
