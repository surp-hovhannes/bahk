# Generated manually on 2026-09-15 for append-only FeastContext history.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_feast_context_versions(apps, schema_editor):
    """Number existing contexts per feast without changing their existing content."""
    FeastContext = apps.get_model("hub", "FeastContext")

    current_feast_id = None
    version = 0
    pending = []
    contexts = FeastContext.objects.order_by(
        "feast_id",
        models.F("time_of_generation").asc(nulls_first=True),
        "pk",
    )

    for context in contexts.iterator(chunk_size=1000):
        if context.feast_id != current_feast_id:
            current_feast_id = context.feast_id
            version = 0

        version += 1
        context.version = version
        context.operation = "generated"
        context.created_by_id = None
        context.additional_instructions = ""
        context.restored_from_id = None
        pending.append(context)

        if len(pending) == 1000:
            FeastContext.objects.bulk_update(
                pending,
                [
                    "version",
                    "operation",
                    "created_by",
                    "additional_instructions",
                    "restored_from",
                ],
            )
            pending = []

    if pending:
        FeastContext.objects.bulk_update(
            pending,
            [
                "version",
                "operation",
                "created_by",
                "additional_instructions",
                "restored_from",
            ],
        )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("hub", "0068_clear_fast_designation_on_commemorations"),
    ]

    operations = [
        migrations.AddField(
            model_name="feastcontext",
            name="additional_instructions",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Editorial instructions supplied when this version was generated",
            ),
        ),
        migrations.AddField(
            model_name="feastcontext",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                help_text="The staff user who created this context version",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_feast_contexts",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="feastcontext",
            name="operation",
            field=models.CharField(
                choices=[
                    ("generated", "Generated"),
                    ("regenerated", "Regenerated"),
                    ("manual_edit", "Manual edit"),
                    ("restored", "Restored"),
                ],
                default="generated",
                help_text="The operation that created this context version",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="feastcontext",
            name="restored_from",
            field=models.ForeignKey(
                blank=True,
                help_text="The prior version restored into this context version",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="restored_versions",
                to="hub.feastcontext",
            ),
        ),
        migrations.AddField(
            model_name="feastcontext",
            name="version",
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.RunPython(
            backfill_feast_context_versions,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="feastcontext",
            name="version",
            field=models.PositiveIntegerField(
                default=1,
                help_text="Monotonic version number within this feast's context history",
            ),
        ),
        migrations.AddConstraint(
            model_name="feastcontext",
            constraint=models.CheckConstraint(
                condition=models.Q(("version__gte", 1)),
                name="feast_context_version_positive",
            ),
        ),
    ]
