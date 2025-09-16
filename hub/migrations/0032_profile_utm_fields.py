from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0031_alter_devotionalset_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='utm_source',
            field=models.CharField(blank=True, help_text='Last seen UTM source', max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='utm_campaign',
            field=models.CharField(blank=True, help_text='Last seen UTM campaign', max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='join_source',
            field=models.CharField(blank=True, help_text='Join source such as push,email,social', max_length=255, null=True),
        ),
    ]

