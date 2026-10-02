"""Preserve deleted-fast history and represent unknown membership endings."""

import django.db.models.deletion
from django.db import migrations, models


def snapshot_existing_periods(apps, schema_editor):
    alias = schema_editor.connection.alias
    Fast = apps.get_model("hub", "Fast")
    Period = apps.get_model("hub", "FastParticipation")
    Profile = apps.get_model("hub", "Profile")
    memberships = set(Profile.fasts.through.objects.using(alias).values_list("profile_id", "fast_id"))
    for fast in Fast.objects.using(alias).all().iterator():
        end = fast.days.using(alias).filter(church_id=fast.church_id).aggregate(end=models.Max("date"))["end"]
        Period.objects.using(alias).filter(fast_id=fast.pk).update(
            fast_original_id=fast.pk,
            fast_name=fast.name,
            fast_year=fast.year,
            fast_end_date=end,
        )
    for period in Period.objects.using(alias).filter(left_at__isnull=True).iterator():
        if (period.profile_id, period.fast_id) not in memberships:
            Period.objects.using(alias).filter(pk=period.pk).update(ended_at_unknown=True)


class Migration(migrations.Migration):
    dependencies = [("hub", "0071_backfill_fastparticipation")]
    operations = [
        migrations.RemoveConstraint(model_name="fastparticipation", name="unique_open_fast_participation"),
        migrations.AddField(
            model_name="fastparticipation",
            name="ended_at_unknown",
            field=models.BooleanField(
                default=False, db_default=False, help_text="Membership ended but its leave timestamp is unknown."
            ),
        ),
        migrations.AddField(
            model_name="fastparticipation",
            name="fast_original_id",
            field=models.BigIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="fastparticipation",
            name="fast_name",
            field=models.CharField(blank=True, default="", db_default="", max_length=128),
        ),
        migrations.AddField(
            model_name="fastparticipation", name="fast_year", field=models.IntegerField(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="fastparticipation", name="fast_end_date", field=models.DateField(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="fastparticipation", name="fast_deleted_at", field=models.DateTimeField(blank=True, null=True)
        ),
        migrations.AlterField(
            model_name="fastparticipation",
            name="fast",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="participations",
                to="hub.fast",
            ),
        ),
        migrations.AlterField(
            model_name="fastparticipation",
            name="joined_at",
            field=models.DateTimeField(blank=True, null=True, help_text="UTC join timestamp; NULL means unknown."),
        ),
        migrations.AlterField(
            model_name="fastparticipation",
            name="left_at",
            field=models.DateTimeField(
                blank=True, null=True, help_text="UTC leave timestamp; NULL when open or unknown."
            ),
        ),
        migrations.RunPython(snapshot_existing_periods, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="fastparticipation",
            constraint=models.UniqueConstraint(
                condition=models.Q(left_at__isnull=True, ended_at_unknown=False),
                fields=("profile", "fast"),
                name="unique_open_fast_participation",
            ),
        ),
    ]
