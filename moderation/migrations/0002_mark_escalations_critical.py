from django.db import migrations


def mark_escalations_critical(apps, schema_editor):
    """Crisis access now reads only ``moderation_severity``; carry older escalations onto it."""
    PrayerRequest = apps.get_model("prayers", "PrayerRequest")
    PrayerRequest.objects.filter(moderation_result__llm_check__suggested_action="escalate").exclude(
        moderation_severity="critical"
    ).update(moderation_severity="critical")


class Migration(migrations.Migration):
    dependencies = [
        ("moderation", "0001_initial"),
        ("prayers", "0011_prayerlibraryoperation"),
    ]
    operations = [migrations.RunPython(mark_escalations_critical, migrations.RunPython.noop)]
