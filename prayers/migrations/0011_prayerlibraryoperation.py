from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('prayers', '0010_prayer_set_icon'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(
        name='PrayerLibraryOperation',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('key', models.CharField(max_length=36)),
            ('digest', models.CharField(max_length=64)),
            ('result', models.JSONField()),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('church', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='hub.church')),
            ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
        ],
        options={'constraints': [models.UniqueConstraint(fields=('church', 'key'), name='unique_prayer_library_operation')]},
    )]
