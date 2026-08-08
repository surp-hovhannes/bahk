import django.db.models.deletion
import learning_resources.utils
import s3_file_field.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("learning_resources", "0008_article_i18n_recipe_i18n_video_i18n_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="DevotionalVideoUpload",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nonce", models.CharField(editable=False, max_length=64, unique=True)),
                ("storage_key", models.CharField(max_length=1024)),
                ("file_name", models.CharField(max_length=200)),
                ("expected_size", models.PositiveBigIntegerField()),
                ("content_type", models.CharField(max_length=100)),
                ("upload_id", models.CharField(max_length=1024)),
                ("state", models.CharField(choices=[("initialized", "Initialized"), ("ready", "Completed / ready"), ("attached", "Attached"), ("cleaned", "Cleaned")], db_index=True, default="initialized", max_length=16)),
                ("completion_requested_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("attached_at", models.DateTimeField(blank=True, null=True)),
                ("cleaned_at", models.DateTimeField(blank=True, null=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="devotional_video_uploads", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.AlterField(
            model_name="video",
            name="video",
            field=s3_file_field.fields.S3FileField(
                help_text="Supported formats: MP4, WebM. Portrait orientation (9:16). Files up to 500 MiB.",
                upload_to=learning_resources.utils.video_upload_path,
            ),
        ),
    ]
